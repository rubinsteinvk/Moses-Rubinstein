import json, os, re, sys

import torch
import whisperx


def norm(s):
    s = s.lower().replace('’', "'").replace('‘', "'")
    return re.sub(r"[^a-zäöüß'\-]", '', s)


def main(audio_path, source_json, out_json):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    with open(source_json, encoding='utf-8') as f:
        src = json.load(f)

    target = [w for line in src['lines'] for w in line['de']]
    target_text = ' '.join(target)

    print(f'Using known authoritative transcript: {len(target)} words')
    print(f'Loading German forced-alignment model on {device}')

    # Unlike the previous ASR->SequenceMatcher approach, do not ask Whisper to
    # rediscover a text that we already know. Feed the authoritative libretto
    # directly into WhisperX's phoneme-based forced aligner.
    align_model, metadata = whisperx.load_align_model(
        language_code='de', device=device
    )
    audio = whisperx.load_audio(audio_path)
    duration = len(audio) / 16000.0

    segments = [{
        'start': 0.0,
        'end': duration,
        'text': target_text,
    }]

    aligned = whisperx.align(
        segments,
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    observed = []
    for seg in aligned.get('segments', []):
        for w in seg.get('words', []):
            text = (w.get('word') or '').strip()
            if text and 'start' in w and 'end' in w:
                observed.append({
                    'text': text,
                    'norm': norm(text),
                    'start': float(w['start']),
                    'end': float(w['end']),
                })

    # WhisperX normally returns the words in transcript order. Match each
    # returned word to the authoritative word by normalized spelling, while
    # allowing punctuation/orthography differences.
    timing = []
    oi = 0
    for ti, target_word in enumerate(target):
        target_norm = norm(target_word)
        found = None
        for candidate in range(oi, min(oi + 3, len(observed))):
            if observed[candidate]['norm'] == target_norm:
                found = candidate
                break
        if found is None:
            timing.append({
                'index': ti,
                'de': target_word,
                'status': 'unmatched',
            })
            continue

        # Any skipped observed words are retained in diagnostics but not used
        # for the authoritative timing list.
        item = observed[found]
        timing.append({
            'index': ti,
            'de': target_word,
            'start': item['start'],
            'end': item['end'],
            'status': 'forced-aligned',
        })
        oi = found + 1

    matched = [x for x in timing if 'start' in x and 'end' in x]
    out = {
        'source': src['source'],
        'status': 'forced_alignment_pending_manual_verification',
        'audio_duration': duration,
        'authoritative_word_count': len(target),
        'matched_word_count': len(matched),
        'words': timing,
        'unmatched_authoritative_words': [
            {'index': x['index'], 'de': x['de']}
            for x in timing if x.get('status') == 'unmatched'
        ],
        'method': 'WhisperX German phoneme forced alignment using authoritative libretto; no ASR transcription matching',
    }

    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f'Matched {len(matched)}/{len(target)} authoritative words')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON')
    main(*sys.argv[1:])
