import json, os, re, sys
from difflib import SequenceMatcher

import torch
import whisperx


def norm(s):
    s = s.lower().replace('’', "'").replace('‘', "'")
    return re.sub(r"[^a-zäöüß'\-]", '', s)


def main(audio_path, source_json, out_json):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'int8'
    model_size = os.environ.get('MODEL_SIZE', 'small')

    print(f'Using WhisperX model={model_size}, device={device}, compute_type={compute_type}')
    model = whisperx.load_model(model_size, device, compute_type=compute_type, language='de')
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=4, language='de')

    align_model, metadata = whisperx.load_align_model(language_code='de', device=device)
    aligned = whisperx.align(
        result['segments'], align_model, metadata, audio, device,
        return_char_alignments=False
    )

    with open(source_json, encoding='utf-8') as f:
        src = json.load(f)

    target = [w for line in src['lines'] for w in line['de']]
    target_norm = [norm(w) for w in target]

    observed = []
    for seg in aligned.get('segments', []):
        for w in seg.get('words', []):
            text = (w.get('word') or '').strip()
            if text and 'start' in w and 'end' in w:
                observed.append({
                    'text': text,
                    'norm': norm(text),
                    'start': float(w['start']),
                    'end': float(w['end'])
                })

    observed = [x for x in observed if x['norm']]
    a = [x['norm'] for x in observed]
    sm = SequenceMatcher(None, target_norm, a, autojunk=False)

    timing = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for ti, oi in zip(range(i1, i2), range(j1, j2)):
                timing.append({
                    'index': ti, 'de': target[ti],
                    'start': observed[oi]['start'], 'end': observed[oi]['end'],
                    'status': 'auto-aligned'
                })
        elif tag == 'replace' and (i2 - i1) == (j2 - j1):
            for ti, oi in zip(range(i1, i2), range(j1, j2)):
                timing.append({
                    'index': ti, 'de': target[ti],
                    'start': observed[oi]['start'], 'end': observed[oi]['end'],
                    'status': 'auto-aligned-replace'
                })

    timing.sort(key=lambda x: x['index'])
    matched = {x['index'] for x in timing}
    out = {
        'source': src['source'],
        'status': 'automatic_alignment_pending_manual_verification',
        'observed_word_count': len(observed),
        'matched_word_count': len(timing),
        'words': timing,
        'unmatched_authoritative_words': [
            {'index': i, 'de': target[i]} for i in range(len(target)) if i not in matched
        ]
    }
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON')
    main(*sys.argv[1:])
