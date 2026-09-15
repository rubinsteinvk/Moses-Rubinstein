import difflib
import json
import re
import sys

import torch
import whisperx


def norm(s):
    s = s.lower().replace('’', "'").replace('‘', "'")
    return re.sub(r"[^a-zäöüß']", '', s)


def load_target(source_json):
    with open(source_json, encoding='utf-8') as f:
        src = json.load(f)
    target = [w for line in src['lines'] for w in line['de']]
    return src, target


def asr_words(result):
    out = []
    for seg in result.get('segments', []):
        for raw in re.findall(r"\S+", seg.get('text', '')):
            n = norm(raw)
            if n:
                out.append({
                    'text': raw,
                    'norm': n,
                    'start': float(seg['start']),
                    'end': float(seg['end']),
                    'segment_start': float(seg['start']),
                    'segment_end': float(seg['end']),
                })
    return out


def add_anchor_map(target, observed):
    a = [norm(x) for x in target]
    b = [x['norm'] for x in observed]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    anchors = {}
    for block in sm.get_matching_blocks():
        ai, bi, size = block
        for k in range(size):
            anchors[ai + k] = observed[bi + k]
    return anchors, sm


def align_chunk(text, start, end, audio, align_model, metadata, device):
    if not text.strip() or end <= start + 0.05:
        return []
    seg = [{'start': float(start), 'end': float(end), 'text': text}]
    aligned = whisperx.align(
        seg,
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    words = []
    for s in aligned.get('segments', []):
        for w in s.get('words', []):
            if w.get('start') is None or w.get('end') is None:
                continue
            words.append({
                'text': (w.get('word') or '').strip(),
                'norm': norm(w.get('word') or ''),
                'start': float(w['start']),
                'end': float(w['end']),
            })
    return words


def main(audio_path, source_json, out_json, model_size='large-v3'):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'

    src, target = load_target(source_json)
    print(f'Authoritative transcript: {len(target)} words')
    print(f'Loading WhisperX ASR model {model_size} on {device} ({compute_type})')

    audio = whisperx.load_audio(audio_path)
    duration = len(audio) / 16000.0

    # First obtain time-localized ASR segments. These are used only as coarse
    # anchors; the final timings come from forced alignment of the authoritative
    # libretto, constrained between real ASR anchors.
    asr_model = whisperx.load_model(
        model_size, device, compute_type=compute_type, language='de'
    )
    result = asr_model.transcribe(audio, batch_size=4, language='de')
    observed = asr_words(result)
    print(f'ASR produced {len(observed)} coarse words')

    anchors, sm = add_anchor_map(target, observed)
    print(f'Exact normalized anchors: {len(anchors)}/{len(target)}')

    align_model, metadata = whisperx.load_align_model(
        language_code='de', device=device
    )

    timing = [None] * len(target)
    anchor_indices = sorted(anchors)

    # Keep exact ASR matches as anchors, but refine them with the phoneme aligner
    # in small local windows whenever possible.
    for idx in anchor_indices:
        o = anchors[idx]
        timing[idx] = {
            'index': idx,
            'de': target[idx],
            'rough_start': o['start'],
            'rough_end': o['end'],
            'status': 'asr-anchor',
        }

    # Align each gap between neighboring anchors. The window is tied to actual
    # audio locations, preventing the aligner from packing the whole libretto
    # into the first 30-40 seconds as happened with a single 0-duration segment.
    boundaries = [-1] + anchor_indices + [len(target)]
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        gap_start = left + 1
        gap_end = right - 1
        if gap_start > gap_end:
            continue

        start_time = 0.0 if left < 0 else timing[left]['rough_end']
        end_time = duration if right >= len(target) else timing[right]['rough_start']
        if end_time <= start_time + 0.08:
            continue

        text = ' '.join(target[gap_start:gap_end + 1])
        print(f'Aligning target {gap_start}:{gap_end} in {start_time:.2f}-{end_time:.2f}: {text}')
        words = align_chunk(text, start_time, end_time, audio, align_model, metadata, device)

        # Map returned phoneme-aligned words to the authoritative gap.
        aw = [norm(x) for x in target[gap_start:gap_end + 1]]
        bw = [x['norm'] for x in words]
        local_sm = difflib.SequenceMatcher(None, aw, bw, autojunk=False)
        for block in local_sm.get_matching_blocks():
            ai, bi, size = block
            for k in range(size):
                if words[bi + k]['norm'] == aw[ai + k]:
                    timing[gap_start + ai + k] = {
                        'index': gap_start + ai + k,
                        'de': target[gap_start + ai + k],
                        'start': words[bi + k]['start'],
                        'end': words[bi + k]['end'],
                        'status': 'forced-aligned-constrained',
                    }

    # Refine anchors in local windows around their ASR locations. This also
    # gives consistent phoneme boundaries rather than ASR segment boundaries.
    for idx in anchor_indices:
        rough = timing[idx]
        lo = max(0.0, rough['rough_start'] - 1.0)
        hi = min(duration, rough['rough_end'] + 1.0)
        words = align_chunk(target[idx], lo, hi, audio, align_model, metadata, device)
        candidates = [w for w in words if w['norm'] == norm(target[idx])]
        if candidates:
            w = min(candidates, key=lambda x: abs((x['start'] + x['end']) / 2 - (rough['rough_start'] + rough['rough_end']) / 2))
            timing[idx] = {
                'index': idx,
                'de': target[idx],
                'start': w['start'],
                'end': w['end'],
                'status': 'forced-aligned-anchor',
            }
        else:
            timing[idx] = {
                'index': idx,
                'de': target[idx],
                'start': rough['rough_start'],
                'end': rough['rough_end'],
                'status': 'asr-anchor-unrefined',
            }

    # Fill any remaining holes by linear interpolation between neighboring
    # verified timings. These are explicitly marked as interpolated and are not
    # presented as phoneme-level measurements.
    known = [i for i, x in enumerate(timing) if x and 'start' in x and 'end' in x]
    for i in range(len(timing)):
        if timing[i] is not None:
            continue
        prev = max([j for j in known if j < i], default=None)
        nxt = min([j for j in known if j > i], default=None)
        if prev is not None and nxt is not None:
            a, b = timing[prev], timing[nxt]
            frac0 = (i - prev) / (nxt - prev)
            frac1 = (i + 1 - prev) / (nxt - prev)
            s = a['end'] + (b['start'] - a['end']) * frac0
            e = a['end'] + (b['start'] - a['end']) * frac1
        elif prev is not None:
            a = timing[prev]
            span = max(0.05, duration - a['end'])
            s = a['end'] + span * 0.02
            e = a['end'] + span * 0.98
        elif nxt is not None:
            b = timing[nxt]
            span = max(0.05, b['start'])
            s = span * 0.02
            e = span * 0.98
        else:
            s, e = 0.0, duration
        timing[i] = {
            'index': i,
            'de': target[i],
            'start': float(s),
            'end': float(e),
            'status': 'interpolated-pending-verification',
        }

    out = {
        'source': src['source'],
        'status': 'coarse_asr_anchored_forced_alignment_pending_manual_verification',
        'audio_duration': duration,
        'authoritative_word_count': len(target),
        'matched_word_count': sum(1 for x in timing if x.get('status') != 'interpolated-pending-verification'),
        'words': timing,
        'method': 'WhisperX large-v3 German ASR anchors + constrained phoneme forced alignment of authoritative libretto; interpolation only for unresolved words',
        'verification_note': 'Use the full 0:00-2:50.266 recording. Timings are not final until checked against the audible vocal onset/offset.',
    }
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'Wrote {out_json}')


if __name__ == '__main__':
    if len(sys.argv) not in (4, 5):
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON [MODEL_SIZE]')
    main(*sys.argv[1:])
