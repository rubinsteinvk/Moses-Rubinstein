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

def align_chunk(words,start,end,audio,model,meta,device):
    text=' '.join(words)
    if not text or end<=start+0.08:return []
    r=whisperx.align([{'start':float(start),'end':float(end),'text':text}],model,meta,audio,device,return_char_alignments=False)
    out=[]
    for s in r.get('segments',[]):
        for w in s.get('words',[]):
            if w.get('start') is not None and w.get('end') is not None:
                out.append({'text':(w.get('word') or '').strip(),'norm':norm(w.get('word') or ''),'start':float(w['start']),'end':float(w['end'])})
    return out

def main(audio_path,source_json,anchor_json,out_json,model_size='large-v3'):
    device='cuda' if torch.cuda.is_available() else 'cpu'
    src,target=load_target(source_json)
    ad=json.load(open(anchor_json,encoding='utf8'))
    raw={x['index']:x for x in ad['anchors']}
    audio=whisperx.load_audio(audio_path); duration=len(audio)/16000
    model,meta=whisperx.load_align_model(language_code='de',device=device)
    inds=sorted(raw); hard=[]
    for k,i in enumerate(inds):
        if k==0: hard.append(i); continue
        prev=hard[-1]; a=raw[prev]; b=raw[i]
        missing=i-prev-1; gap=b['start']-a['end']
        need=0.11*max(1,missing)+0.08
        if gap>=need or missing==0: hard.append(i)
        else: print(f'Soft anchor {i}: gap {gap:.3f}s for {missing} missing words')
    if hard[-1]!=inds[-1]: hard.append(inds[-1])
    timing=[None]*len(target)
    for i in hard:
        timing[i]={'index':i,'de':target[i],'start':raw[i]['start'],'end':raw[i]['end'],'status':'coarse-anchor'}
    boundaries=[None]+hard+[None]
    for left,right in zip(boundaries[:-1],boundaries[1:]):
        lo=0.0 if left is None else raw[left]['end']
        hi=duration if right is None else raw[right]['start']
        aidx=0 if left is None else left+1
        bidx=len(target)-1 if right is None else right-1
        if aidx>bidx: continue
        ws=max(0.0,lo-0.35); we=min(duration,hi+0.35)
        print(f'Align {aidx}:{bidx} in {ws:.2f}-{we:.2f}')
        out=align_chunk(target[aidx:bidx+1],ws,we,audio,model,meta,device)
        A=[norm(x) for x in target[aidx:bidx+1]]; B=[x['norm'] for x in out]
        sm=difflib.SequenceMatcher(None,A,B,autojunk=False)
        for ai,bi,size in sm.get_matching_blocks():
            for k in range(size):
                w=out[bi+k]; idx=aidx+ai+k
                if w['start']>=lo-0.4 and w['end']<=hi+0.4:
                    timing[idx]={'index':idx,'de':target[idx],'start':w['start'],'end':w['end'],'status':'forced-aligned-window'}
    for i in range(len(target)):
        if timing[i] is not None: continue
        prev=max((j for j in range(i-1,-1,-1) if timing[j]),default=None)
        nxt=min((j for j in range(i+1,len(target)) if timing[j]),default=None)
        lo=timing[prev]['end'] if prev is not None else 0.0
        hi=timing[nxt]['start'] if nxt is not None else duration
        if hi>lo+0.08:
            out=align_chunk([target[i]],max(0,lo-.2),min(duration,hi+.2),audio,model,meta,device)
            cand=[w for w in out if w['norm']==norm(target[i])]
            if cand:
                w=cand[0]; timing[i]={'index':i,'de':target[i],'start':w['start'],'end':w['end'],'status':'forced-aligned-single'}
    for i in range(len(target)):
        if timing[i] is not None: continue
        prev=max((j for j in range(i-1,-1,-1) if timing[j]),default=None)
        nxt=min((j for j in range(i+1,len(target)) if timing[j]),default=None)
        lo=timing[prev]['end'] if prev is not None else 0.0
        hi=timing[nxt]['start'] if nxt is not None else duration
        count=(nxt-prev-1) if prev is not None and nxt is not None else 1
        pos=(i-prev) if prev is not None else i+1
        s=lo+(hi-lo)*(pos-1)/max(1,count); e=lo+(hi-lo)*pos/max(1,count)
        timing[i]={'index':i,'de':target[i],'start':float(s),'end':float(e),'status':'interpolated-pending-verification'}
    for i in range(1,len(target)):
        if timing[i]['start']<timing[i-1]['end']:
            timing[i]['start']=timing[i-1]['end']
            timing[i]['end']=max(timing[i]['end'],timing[i]['start']+0.01)
    matched=sum(x['status']!='interpolated-pending-verification' for x in timing)
    out={'source':src['source'],'status':'anchor_constrained_phrase_alignment_pending_manual_verification','audio_duration':duration,'authoritative_word_count':len(target),'matched_word_count':matched,'hard_anchor_count':len(hard),'words':timing,'method':'First-pass distributed ASR anchors as coarse boundaries; WhisperX German phoneme forced alignment phrase-by-phrase; implausibly tight anchors treated as soft.','verification_note':'Full recording 0:00-2:50.266. Manual listening verification still required.'}
    json.dump(out,open(out_json,'w',encoding='utf8'),ensure_ascii=False,indent=2)
    print(f'Wrote {out_json}: {matched}/{len(target)} measured, {len(hard)} hard anchors')

if __name__=='__main__':
    args=sys.argv[1:]
    if len(args)==4:
        audio_path,source_json,out_json,model_size=args
        main(audio_path,source_json,'data/picture5-anchors.json',out_json,model_size)
    elif len(args)==5:
        main(*args)
    else:
        raise SystemExit('usage: align_picture5.py AUDIO SOURCE_JSON OUTPUT_JSON MODEL_SIZE OR AUDIO SOURCE_JSON ANCHORS_JSON OUTPUT_JSON MODEL_SIZE')
