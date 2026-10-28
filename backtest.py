"""Walk-forward backtest on real Wayback-archived public list prices. No leakage:
each forecast at origin t uses only snapshots dated <= t."""
import json,math,datetime as dt,statistics as st,sys
S=json.load(open(sys.argv[1] if len(sys.argv)>1 else 'hist/series.json'))
KEEP=['lambda|H100','lambda|A100','lambda|B200','runpod|H100','runpod|A100','runpod|H200','runpod|L40S']
D=lambda s:dt.date.fromisoformat(s)
def clean(v):
  ks=sorted(v);x=[v[k] for k in ks];out={}
  for i,k in enumerate(ks):
    nb=[x[j] for j in (i-1,i+1) if 0<=j<len(x)]
    if len(nb)==2 and abs(x[i]/st.median(nb)-1)>.4 and abs(nb[0]/nb[1]-1)<.1: continue  # isolated parser glitch
    out[k]=x[i]
  return out
def daily(v):
  ks=sorted(v);d0,d1=D(ks[0]),D(ks[-1]);r={};cur=None;i=0
  for n in range((d1-d0).days+1):
    d=d0+dt.timedelta(n)
    while i<len(ks) and D(ks[i])<=d: cur=v[ks[i]];i+=1
    r[d]=cur
  return r
def holt(y,phi=.9,a=.5,b=.2):
  l=math.log(y[0]);t=0
  for v in y[1:]:
    lv=math.log(v);pl=l;l=a*lv+(1-a)*(l+phi*t);t=b*(l-pl)+(1-b)*phi*t
  return l,t
def fc(hist,h,phi=.9):
  w=[hist[i] for i in range(len(hist)-1,-1,-7)][::-1][-26:]  # weekly, last 26 weeks
  if len(w)<4: return hist[-1]
  l,t=holt(w,phi);steps=h/7;damp=sum(phi**k for k in range(1,int(math.ceil(steps))+1))*(steps/max(1,math.ceil(steps)))
  return math.exp(l+damp*t)
H=[1,7,30];res={h:{'m':[],'n':[]} for h in H};per={}
for k in KEEP:
  s=daily(clean(S[k]));days=sorted(s);obs=set(D(x) for x in S[k])
  resid={h:[] for h in H};nres={h:[] for h in H}
  for oi in range(60,len(days),7):
    o=days[oi];hist=[s[d] for d in days[:oi+1]]
    for h in H:
      tgt=o+dt.timedelta(h)
      if tgt>days[-1]: continue
      y=s[tgt];p=fc(hist,h);n=hist[-1]
      e=math.log(y/p);r=resid[h]
      if len(r)>=8: lo,hi=sorted(r)[int(.1*len(r))],sorted(r)[min(len(r)-1,int(.9*len(r)))]
      else: lo,hi=-.08*math.sqrt(h/7+.1),.08*math.sqrt(h/7+.1)
      cov=lo<=e<=hi;r.append(e)
      sg=lambda z:0 if abs(z)<.01 else (1 if z>0 else -1)
      res[h]['m'].append(dict(k=k,o=str(o),ape=abs(y-p)/y,cov=cov,dir=sg(p/n-1)==sg(y/n-1),moved=sg(y/n-1)!=0,lo=p*math.exp(lo),hi=p*math.exp(hi),p=p,y=y))
      en=math.log(y/n);rn=nres[h]
      if len(rn)>=8: nlo,nhi=sorted(rn)[int(.1*len(rn))],sorted(rn)[min(len(rn)-1,int(.9*len(rn)))]
      else: nlo,nhi=-.08*math.sqrt(h/7+.1),.08*math.sqrt(h/7+.1)
      nlo=min(nlo,-{1:.02,7:.04,30:.08}[h]);nhi=max(nhi,{1:.02,7:.04,30:.08}[h]);rn.append(en)
      res[h]['n'].append(dict(k=k,ape=abs(y-n)/y,dir=sg(y/n-1)==0,moved=sg(y/n-1)!=0,cov=nlo<=en<=nhi,e=en))
summ={}
for h in H:
  m,n=res[h]['m'],res[h]['n']
  mv=[x for x in m if x['moved']]
  summ[h]=dict(n=len(m),mape_model=round(100*st.mean(x['ape'] for x in m),2),mape_naive=round(100*st.mean(x['ape'] for x in n),2),
    coverage80=round(100*st.mean(x['cov'] for x in m),1),coverage80_naive_floored=round(100*st.mean(x['cov'] for x in n),1),dir_acc_model=round(100*st.mean(x['dir'] for x in m),1),dir_acc_naive=round(100*st.mean(x['dir'] for x in n),1),
    moves=len(mv),dir_acc_on_moves=round(100*st.mean(x['dir'] for x in mv),1) if mv else None)
  print(h,summ[h])
bys={}
for k in KEEP:
  r=[x for x in res[30]['m'] if x['k']==k];nn=[x for x in res[30]['n'] if x['k']==k]
  bys[k]=dict(n=len(r),mape_model=round(100*st.mean(x['ape'] for x in r),2),mape_naive=round(100*st.mean(x['ape'] for x in nn),2),coverage80=round(100*st.mean(x['cov'] for x in r),1))
  print(k,bys[k])
q={h:sorted(x['e'] for x in res[h]['n']) for h in H}
band={h:[round(q[h][int(.1*len(q[h]))],4),round(q[h][int(.9*len(q[h]))],4)] for h in H}
ser={k:sorted(clean(S[k]).items()) for k in KEEP}
path={k:[dict(o=x['o'],p=round(x['p'],3),lo=round(x['lo'],3),hi=round(x['hi'],3),y=x['y']) for x in res[30]['m'] if x['k']==k] for k in KEEP}
json.dump(dict(label='BACKTEST (walk-forward, Wayback-archived public list prices)',generated=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
  sources={'lambda':'web.archive.org snapshots of lambdalabs.com/service/gpu-cloud, lambda.ai/service/gpu-cloud, lambda.ai/pricing','runpod':'web.archive.org snapshots of runpod.io/pricing'},
  model='Damped Holt (phi .9) on weekly log list price; 80% interval = walk-forward empirical residual quantiles (min 8 past residuals, else ±8%·sqrt(h/7))',
  baseline='Naive: last observed list price',horizons=summ,bySeries30d=bys,pooledLogBand80=band,series=ser,path30=path),open('backtest.json','w'))
