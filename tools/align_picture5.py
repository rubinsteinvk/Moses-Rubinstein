import difflib
import json
import re
import sys
import torch
import whisperx


def norm(s):
    s = s.lower().replace('’', "'").replace('‘', "'")
    return re.sub(r"[^a-zäöüß']", '', s)


def load_target(p):
    src = json.load(open(p, encoding='utf8'))
    return src, [w for line in src['lines'] for w in line['de']]


def main(audio_path, source_json, out_json, model_size='large-v3'):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'
    src, target = load_target(source_json)
    audio = whisperx.load_audio(audio_path)
    duration = len(audio) / 16000
    print(f'Audio duration: {duration:.3f}s; target words: {len(target)}')
    print(f'Loading Whisper {model_size} on {device} ({compute_type})')

    asr = whisperx.load_model(model_size, device=device, compute_type=compute_type, language='de')
    result = asr.transcribe(audio, batch_size=8)
    del asr
    print(f'Whisper segments: {len(result.get("segments", []))}')

    align_model, meta = whisperx.load_align_model(language_code='de', device=device)
    aligned = whisperx.align(
        result['segments'], align_model, meta, audio, device,
        return_char_alignments=False
    )

    asr_words = []
    asr_segments = []
    for seg in aligned.get('segments', []):
        asr_segments.append({
            'start': seg.get('start'),
            'end': seg.get('end'),
            'text': (seg.get('text') or '').strip()
        })
        for w in seg.get('words', []):
            if w.get('start') is None or w.get('end') is None:
                continue
            txt = (w.get('word') or '').strip()
            n = norm(txt)
            if n:
                asr_words.append({'text': txt, 'norm': n,
                                  'start': float(w['start']), 'end': float(w['end'])})

    print(f'ASR word timestamps: {len(asr_words)}')

    # First pass: exact sequence blocks plus conservative fuzzy recovery.
    A = [norm(w) for w in target]
    B = [w['norm'] for w in asr_words]
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    timing = [None] * len(target)
    pairs = []
    for ai, bi, size in sm.get_matching_blocks():
        for k in range(size):
            ti, wi = ai + k, bi + k
            timing[ti] = {'index': ti, 'de': target[ti],
                          'start': asr_words[wi]['start'], 'end': asr_words[wi]['end'],
                          'status': 'full-asr-exact'}
            pairs.append((ti, wi))

    for i, w in enumerate(target):
        if timing[i] is not None:
            continue
        prev = max((j for j in range(i - 1, -1, -1) if timing[j]), default=None)
        nxt = min((j for j in range(i + 1, len(target)) if timing[j]), default=None)
        lo = timing[prev]['end'] if prev is not None else 0.0
        hi = timing[nxt]['start'] if nxt is not None else duration
        if hi <= lo:
            continue
        cand = []
        for x in asr_words:
            if x['start'] >= lo - 0.6 and x['end'] <= hi + 0.6:
                score = difflib.SequenceMatcher(None, norm(w), x['norm'], autojunk=False).ratio()
                if score >= 0.72:
                    cand.append((score, x))
        if cand:
            score, x = max(cand, key=lambda z: z[0])
            timing[i] = {'index': i, 'de': w, 'start': x['start'], 'end': x['end'],
                         'status': f'fuzzy-asr-{score:.2f}'}

    # Second pass: forced alignment of the authoritative text for each still-unresolved
    # contiguous gap. This uses the actual audio and the phoneme aligner, rather than
    # inventing/interpolating timestamps from neighboring words.
    unresolved_before = sum(x is None for x in timing)
    runs = []
    i = 0
    while i < len(timing):
        if timing[i] is not None:
            i += 1
            continue
        j = i
        while j + 1 < len(timing) and timing[j + 1] is None:
            j += 1
        runs.append((i, j))
        i = j + 1

    print(f'Forced-alignment gaps: {len(runs)}')
    for a, b in runs:
        prev = max((j for j in range(a - 1, -1, -1) if timing[j]), default=None)
        nxt = min((j for j in range(b + 1, len(target)) if timing[j]), default=None)
        lo = timing[prev]['end'] if prev is not None else 0.0
        hi = timing[nxt]['start'] if nxt is not None else duration
        if hi <= lo + 0.05:
            continue
        text = ' '.join(target[a:b + 1])
        print(f'Force aligning {a}-{b}: {text!r} in {lo:.3f}-{hi:.3f}')
        try:
            segs = [{'start': lo, 'end': hi, 'text': text}]
            forced = whisperx.align(segs, align_model, meta, audio, device,
                                    return_char_alignments=False)
            got = []
            for seg in forced.get('segments', []):
                for w in seg.get('words', []):
                    if w.get('start') is not None and w.get('end') is not None:
                        got.append({'text': (w.get('word') or '').strip(),
                                    'norm': norm(w.get('word') or ''),
                                    'start': float(w['start']), 'end': float(w['end'])})
            TA = [norm(x) for x in target[a:b + 1]]
            GA = [x['norm'] for x in got]
            gsm = difflib.SequenceMatcher(None, TA, GA, autojunk=False)
            for aa, gg, size in gsm.get_matching_blocks():
                for k in range(size):
                    ti = a + aa + k
                    x = got[gg + k]
                    if timing[ti] is None and x['start'] >= lo and x['end'] <= hi:
                        timing[ti] = {'index': ti, 'de': target[ti],
                                      'start': x['start'], 'end': x['end'],
                                      'status': 'forced-text-alignment'}
            print(f'  forced words={len(got)}, recovered={sum(timing[k] is not None for k in range(a,b+1))}/{b-a+1}')
        except Exception as e:
            print(f'  forced alignment failed: {e}')

    unresolved = []
    for i, w in enumerate(target):
        if timing[i] is None:
            unresolved.append(i)
            timing[i] = {'index': i, 'de': w, 'start': None, 'end': None, 'status': 'unresolved'}

    matched = sum(x['start'] is not None for x in timing)
    out = {
        'source': src['source'],
        'status': 'forced_text_alignment_pending_manual_verification',
        'audio_duration': duration,
        'authoritative_word_count': len(target),
        'asr_word_count': len(asr_words),
        'matched_word_count': matched,
        'unresolved_indices': unresolved,
        'words': timing,
        'asr_words': asr_words,
        'asr_segments': asr_segments,
        'method': 'Full-recording Whisper/WhisperX transcription followed by authoritative-text matching; unresolved contiguous gaps are then aligned directly against the authoritative German text within the real neighboring audio interval using WhisperX phoneme alignment. No interpolation.',
        'verification_note': 'Full recording 0:00-2:50.266. Forced-text timings require listening verification before publication.'
    }
    json.dump(out, open(out_json, 'w', encoding='utf8'), ensure_ascii=False, indent=2)
    print(f'Wrote {out_json}: {matched}/{len(target)} matched; unresolved={len(unresolved)}; first-pass-unresolved={unresolved_before}')


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) == 3:
        main(*args)
    elif len(args) == 4:
        main(*args)
    else:
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON [MODEL_SIZE]')
