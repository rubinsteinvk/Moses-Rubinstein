import difflib
import json
import re
import sys
import torch
import whisperx

def norm(s):
    s=s.lower().replace('’',"'").replace('‘',"'")
    return re.sub(r"[^a-zäöüß']",'',s)

def load_target(p):
    src=json.load(open(p,encoding='utf8'))
    return src,[w for line in src['lines'] for w in line['de']]

def main(audio_path,source_json,out_json,model_size='large-v3'):
    device='cuda' if torch.cuda.is_available() else 'cpu'
    compute_type='float16' if device=='cuda' else 'int8'
    src,target=load_target(source_json)
    audio=whisperx.load_audio(audio_path)
    duration=len(audio)/16000
    print(f'Audio duration: {duration:.3f}s; target words: {len(target)}')
    print(f'Loading Whisper {model_size} on {device} ({compute_type})')
    asr=whisperx.load_model(model_size,device=device,compute_type=compute_type,language='de')
    result=asr.transcribe(audio,batch_size=8)
    del asr
    print(f'Whisper segments: {len(result.get("segments",[]))}')
    align_model,meta=whisperx.load_align_model(language_code='de',device=device)
    aligned=whisperx.align(result['segments'],align_model,meta,audio,device,return_char_alignments=False)
    del align_model
    asr_words=[]
    for seg in aligned.get('segments',[]):
        for w in seg.get('words',[]):
            if w.get('start') is None or w.get('end') is None: continue
            txt=(w.get('word') or '').strip(); n=norm(txt)
            if n: asr_words.append({'text':txt,'norm':n,'start':float(w['start']),'end':float(w['end'])})
    print(f'ASR word timestamps: {len(asr_words)}')
    A=[norm(w) for w in target]; B=[w['norm'] for w in asr_words]
    sm=difflib.SequenceMatcher(None,A,B,autojunk=False)
    timing=[None]*len(target); pairs=[]
    for ai,bi,size in sm.get_matching_blocks():
        for k in range(size):
            ti=ai+k; wi=bi+k
            timing[ti]={'index':ti,'de':target[ti],'start':asr_words[wi]['start'],'end':asr_words[wi]['end'],'status':'full-asr-exact'}
            pairs.append((ti,wi))
    print(f'Exact sequence matches: {len(pairs)}/{len(target)}')
    for i,w in enumerate(target):
        if timing[i] is not None: continue
        prev=max((j for j in range(i-1,-1,-1) if timing[j]),default=None)
        nxt=min((j for j in range(i+1,len(target)) if timing[j]),default=None)
        lo=timing[prev]['end'] if prev is not None else 0.0
        hi=timing[nxt]['start'] if nxt is not None else duration
        if hi<=lo: continue
        cand=[]
        for j,x in enumerate(asr_words):
            if x['start']>=lo-0.6 and x['end']<=hi+0.6:
                score=difflib.SequenceMatcher(None,norm(w),x['norm'],autojunk=False).ratio()
                if score>=0.72: cand.append((score,j,x))
        if cand:
            score,j,x=max(cand,key=lambda z:z[0])
            timing[i]={'index':i,'de':w,'start':x['start'],'end':x['end'],'status':f'fuzzy-asr-{score:.2f}'}
    unresolved=[]
    for i,w in enumerate(target):
        if timing[i] is None:
            unresolved.append(i)
            timing[i]={'index':i,'de':w,'start':None,'end':None,'status':'unresolved'}
    matched=sum(x['start'] is not None for x in timing)
    out={'source':src['source'],'status':'full_recording_asr_word_alignment_pending_manual_verification','audio_duration':duration,'authoritative_word_count':len(target),'asr_word_count':len(asr_words),'matched_word_count':matched,'unresolved_indices':unresolved,'words':timing,'method':'Full-recording Whisper German transcription followed by WhisperX phoneme alignment; authoritative libretto matched to actual ASR word sequence. No interpolation.','verification_note':'Full recording 0:00-2:50.266. Timings require listening verification before publication.'}
    json.dump(out,open(out_json,'w',encoding='utf8'),ensure_ascii=False,indent=2)
    print(f'Wrote {out_json}: {matched}/{len(target)} matched; unresolved={len(unresolved)}')

if __name__=='__main__':
    args=sys.argv[1:]
    if len(args)==3: main(*args)
    elif len(args)==4: main(*args)
    else: raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON [MODEL_SIZE]')
