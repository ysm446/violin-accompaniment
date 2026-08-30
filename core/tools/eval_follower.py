"""オンライン追従器の評価。

  cd core && .venv/Scripts/python tools/eval_follower.py            設定の格子評価
  cd core && .venv/Scripts/python tools/eval_follower.py --trace    テンポ変動シナリオの失敗フレーム
  cd core && .venv/Scripts/python tools/eval_follower.py --trace2 [gain] / --trace3   途中開始+弾き直し / 実演奏のトレース

シナリオ: (1) 合成演奏・テンポ 65〜95 で変動(拍 50.5〜63.75 の B5 ×21 の同音連打を含む。in-run はその区間、
out はそれ以外)、(2) 合成・拍 32 に置いて(譜面クリック相当)開始し、48 で 40 に弾き直し、(3) 実演奏の記録(オフライン整列を正解とする。
連打区間ではオフライン整列自体が不確かなので目安)。誤差は真のテンポで ms に換算。
"""
import sys, itertools
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'core'))
from violin_core.score_notes import load_part_notes
from violin_core.synth import render_performance
from violin_core.replay import extract_offline
from violin_core.follower import OnlineFollower
sr=48000; hop=512; fps=sr/hop
notes=load_part_notes(ROOT / 'scores/vivaldi_spring_1/score.mid')
def feats_of(audio):
    F=extract_offline(audio, sr, 4096, hop); T=(np.arange(len(F['flux']))+1)/fps; return F,T
def run(F,T,start=0.0,**kw):
    f=OnlineFollower(notes, 70.0, fps, **kw); f.reset(start); pos=[]; raw=[]; conf=[]
    for i in range(len(T)):
        st=f.process(F['chroma'][i], float(F['flux'][i]), float(F['level_db'][i]), t=T[i]); pos.append(st.position); raw.append(st.raw_position); conf.append(st.confidence)
    return np.array(pos), np.array(raw), np.array(conf)
def metrics(pos,true,active,bpm):
    e=(pos-true)*60.0/bpm*1000; a=active; return np.median(np.abs(e[a])), np.percentile(np.abs(e[a]),95), np.median(e[a])
# 1: テンポ変動 65-95
rng=np.random.default_rng(1); det={i: float(rng.normal(0,15)) for i in range(len(notes))}; bpm=lambda b: 80-15*np.sin(b/20)
a1,t1=render_performance(notes, sr, bpm_curve=bpm, detune_cents=lambda i: det[i], timing_jitter_ms=20, max_beats=80)
F1,T1=feats_of(a1); tb=np.array([n.beat for n in notes[:len(t1)]]); tt=np.array([x.onset for x in t1]); TR1=np.interp(T1,tt,tb)
A1=(F1['level_db']>-45)&(T1>=tt[0])&(T1<=tt[-1]); B1=np.array([bpm(b) for b in TR1])
IN1=(TR1>=50.5)&(TR1<63.75)  # 同音連打の区間
# 2: 途中開始(拍 32)+ 1.5 秒の停止 + 弾き直し(48 → 40)
sub=[n for n in notes if 32<=n.beat<64]; a2,t2=render_performance(sub, sr, bpm_curve=lambda b: 75.0, lead_silence=0.5)
cut_i=[i for i,n in enumerate(sub) if n.beat>=48][0]; t_cut=t2[cut_i].onset
sub_b=[n for n in notes if 40<=n.beat<56]; a3,t3=render_performance(sub_b, sr, bpm_curve=lambda b: 75.0, lead_silence=1.5)
seg=np.concatenate([a2[:int(t_cut*sr)], a3]); F2,T2=feats_of(seg)
tb1=np.array([n.beat for n in sub]); tt1=np.array([x.onset for x in t2]); m1=tt1<t_cut
tb2=np.array([n.beat for n in sub_b]); tt2=np.array([x.onset for x in t3])+t_cut
TR2=np.where(T2<t_cut, np.interp(T2,tt1[m1],tb1[m1]), np.interp(T2,tt2,tb2)); A2=(F2['level_db']>-45)&(T2>=tt1[0])&(T2<=tt2[-1])
# 3: 実演奏(オフライン整列を正解に)
from violin_core.align import analyze_session
sess=ROOT / 'recordings/20260830-042258'; f=np.load(sess/'features.npz'); T3=(np.arange(len(f['flux']))+1)/fps
r=analyze_session(sess, Path(ROOT / 'scores/vivaldi_spring_1/score.mid')); pb=np.array(r['path_beats']); TR3=np.interp(T3, np.arange(len(pb))*int(fps/10)/fps, pb)
F3={'chroma':f['chroma'],'flux':f['flux'],'level_db':f['level_db']}; A3=(f['level_db']>-45)&(T3>2)&(T3<50)
def evaluate(**kw):
    p1,_,_=run(F1,T1,**kw); m1_=metrics(p1,TR1,A1,B1); mi=metrics(p1,TR1,A1&IN1,B1); mo=metrics(p1,TR1,A1&~IN1,B1)
    p2,_,_=run(F2,T2,start=32.0,**kw); mb=metrics(p2,TR2,A2&(T2<t_cut),75.0); ma=metrics(p2,TR2,A2&(T2>t_cut+2.0),75.0)
    e=np.abs(p2-TR2); idx=np.nonzero((T2>t_cut+1.5)&(e<0.5)&A2)[0]; re=(T2[idx[0]]-t_cut-1.5) if len(idx) else -1
    p3,_,_=run(F3,T3,**kw); m3=metrics(p3,TR3,A3,82.0)
    return m1_, mb, ma, re, m3, mi, mo
if __name__=='__main__':
    if '--trace2' in sys.argv:
        arg_i=sys.argv.index('--trace2')+1
        gain=float(sys.argv[arg_i]) if arg_i<len(sys.argv) and not sys.argv[arg_i].startswith('--') else 0.25
        f=OnlineFollower(notes, 70.0, fps, measurement_gain=gain)
        print('scenario 2 (start@32, restart 48->40 at t=%.1f), gain=%.2f' % (t_cut, gain))
        print('    t   true    raw    pos  conf  D@true D@raw  level')
        for i in range(len(T2)):
            st=f.process(F2['chroma'][i], float(F2['flux'][i]), float(F2['level_db'][i]), t=T2[i])
            if i % int(fps/2)==0 and (T2[i]<12 or abs(T2[i]-t_cut)<8):
                jt=min(int(round(TR2[i]/f.ref_step)), len(f.D)-1); jr=int(round(st.raw_position/f.ref_step))
                print('%6.1f %6.1f %6.1f %6.1f %5.2f %7.1f %6.1f %6.0f' % (T2[i], TR2[i], st.raw_position, st.position, st.confidence, f.D[jt], f.D[jr], F2['level_db'][i]))
        sys.exit()
    if '--trace3' in sys.argv:
        f=OnlineFollower(notes, 70.0, fps)
        print('real recording: every 1 s: t, true(offline), raw, pos, conf, D@true, D@raw, level')
        for i in range(len(T3)):
            st=f.process(F3['chroma'][i], float(F3['flux'][i]), float(F3['level_db'][i]), t=T3[i])
            if i % int(fps)==0:
                jt=min(int(round(TR3[i]/f.ref_step)), len(f.D)-1); jr=int(round(st.raw_position/f.ref_step))
                print('%5.0f %6.1f %6.1f %6.1f %5.2f %6.1f %6.1f %5.0f' % (T3[i], TR3[i], st.raw_position, st.position, st.confidence, f.D[jt], f.D[jr], F3['level_db'][i]))
        sys.exit()
    if '--trace' in sys.argv:
        p,raw,conf=run(F1,T1); e=(p-TR1)*60/B1*1000
        bad=np.nonzero(A1&(np.abs(e)>300))[0]
        print('tempo-varying frames with |err|>300ms:', len(bad), 'of', A1.sum())
        for i in bad[::10][:40]: print('  t=%.2f true %.2f raw %.2f pos %.2f conf %.2f err %+.0f ms' % (T1[i], TR1[i], raw[i], p[i], conf[i], e[i]))
    else:
        print('%-22s| tempo-var med p95 bias | in-run med p95 | out med p95 | restart before after reanchor | real med p95 bias' % 'config')
        for ow,gain in itertools.product((0.5,1.0),(0.25,0.5)):
            m1_,mb,ma,re,m3,mi,mo=evaluate(onset_weight=ow, measurement_gain=gain)
            print('onset %.1f gain %.2f    | %5.0f %5.0f %+5.0f | %5.0f %5.0f | %5.0f %5.0f | %5.0f %5.0f %5.2fs | %5.0f %5.0f %+5.0f' % (ow,gain,*m1_,mi[0],mi[1],mo[0],mo[1],mb[0],ma[0],re,*m3))
