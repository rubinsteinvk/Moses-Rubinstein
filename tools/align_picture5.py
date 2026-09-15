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
        # Critical: use Whisper's actual word timestamps when available.
        # Segment-level start/end for every word collapses an entire sung phrase
        # to one timestamp and was the main defect in the previous run.
        seg_words = seg.get('words') or []
        if seg_words:
            for w in seg_words:
                raw = (w.get('word') or '').strip()
                n = norm(raw)
                if n and w.get('start') is not None and w.get('end') is not None:
                    out.append({
                        'text': raw,
                        'norm': n,
                        'start': float(w['start']),
                        'end': float(w['end']),
                        'segment_start': float(seg['start']),
                        'segment_end': float(seg['end']),
                    })
        else:
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
    return anchors


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

    asr_model = whisperx.load_model(
        model_size, device, compute_type=compute_type, language='de'
    )
    # Request word-level timestamps. These are the coarse temporal anchors;
    # the final boundaries are subsequently refined by phoneme alignment.
    result = asr_model.transcribe(
        audio, batch_size=4, language='de', word_timestamps=True
    )
    observed = asr_words(result)
    print(f'ASR produced {len(observed)} word-level coarse words')

    anchors = add_anchor_map(target, observed)
    print(f'Exact normalized anchors: {len(anchors)}/{len(target)}')

    align_model, metadata = whisperx.load_align_model(
        language_code='de', device=device
    )

    timing = [None] * len(target)
    anchor_indices = sorted(anchors)

    for idx in anchor_indices:
        o = anchors[idx]
        timing[idx] = {
            'index': idx,
            'de': target[idx],
            'rough_start': o['start'],
            'rough_end': o['end'],
            'status': 'asr-anchor',
        }

    # Align each unresolved run only inside the real interval between its
    # neighboring ASR word anchors. This prevents global time compression.
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

    # Refine every ASR anchor in a small local window, selecting the candidate
    # closest to Whisper's actual word timestamp.
    for idx in anchor_indices:
        rough = timing[idx]
        lo = max(0.0, rough['rough_start'] - 1.5)
        hi = min(duration, rough['rough_end'] + 1.5)
        words = align_chunk(target[idx], lo, hi, audio, align_model, metadata, device)
        candidates = [w for w in words if w['norm'] == norm(target[idx])]
        if candidates:
            center = (rough['rough_start'] + rough['rough_end']) / 2
            w = min(candidates, key=lambda x: abs((x['start'] + x['end']) / 2 - center))
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

    # Remaining words are interpolated only between genuine word-level anchors.
    # They remain explicitly marked as needing verification.
    known = [i for i, x in enumerate(timing) if x and 'start' in x and 'end' in x]
    for i in range(len(timing)):
        if timing[i] is not None:
            continue
        prev = max([j for j in known if j < i], default=None)
        nxt = min([j for j in known if j > i], default=None)
        if prev is not None and nxt is not None:
            a, b = timing[prev], timing[nxt]
            gap = max(0.0, b['start'] - a['end'])
            n = nxt - prev
            s = a['end'] + gap * ((i - prev) / n)
            e = a['end'] + gap * ((i + 1 - prev) / n)
        elif prev is not None:
            a = timing[prev]
            s = a['end']
            e = duration if i == len(timing) - 1 else s
        elif nxt is not None:
            b = timing[nxt]
            s = 0.0 if i == 0 else b['start']
            e = b['start']
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
        'status': 'word_timestamp_asr_anchored_forced_alignment_pending_manual_verification',
        'audio_duration': duration,
        'authoritative_word_count': len(target),
        'matched_word_count': sum(1 for x in timing if x.get('status') != 'interpolated-pending-verification'),
        'words': timing,
        'method': 'WhisperX German word-timestamp ASR anchors + constrained phoneme forced alignment of authoritative libretto',
        'verification_note': 'Full vocal recording is 0:00-2:50.266. Word timestamps must still be checked against audible onset and offset before publication.',
    }
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'Wrote {out_json}')


if __name__ == '__main__':
    if len(sys.argv) not in (4, 5):
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON [MODEL_SIZE]')
    main(*sys.argv[1:])
