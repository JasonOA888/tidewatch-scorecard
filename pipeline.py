"""Tidewatch daily forecast pipeline: snapshot public GPU prices -> index -> forecast -> commit (SHA-256, OpenTimestamps,
optional BSC testnet tx) -> reveal & score matured forecasts. Public data only. Stdlib only (eth-account optional)."""
import json,re,os,sys,math,hashlib,time,urllib.request,urllib.parse,statistics as st,datetime as dt,glob,subprocess
START=dt.date(2026,10,28); CLASSES=['H100','H200','B200','A100','L40S','RTX 4090']; H=[1,7,30]
TODAY=dt.date.fromisoformat(os.environ.get('TW_DATE') or dt.datetime.now(dt.timezone.utc).date().isoformat())
UA={'User-Agent':'Mozilla/5.0 (tidewatch-scorecard; public price snapshot)','Accept-Encoding':'identity'}
def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def get(url,data=None,headers={},raw=False):
  h=dict(UA);h.update(headers);r=urllib.request.urlopen(urllib.request.Request(url,data=data,headers=h),timeout=45).read()
  if r[:2]==b'\x1f\x8b':
    import gzip;r=gzip.decompress(r)
  return r.decode() if raw else json.loads(r)
def cls(name):
  n=name.upper()
  if 'NVL' in n or 'GH200' in n: return None
  for c,rx in [('B200',r'B200'),('H200',r'H200'),('H100',r'H100(?!.*PCI)'),('A100',r'A100(?!.*40 ?GB)'),('L40S',r'L40S'),('RTX 4090',r'4090')]:
    if re.search(rx,n): return c
SRC={}
def vast():
  q=lambda a,p: sorted(a)[min(len(a)-1,int(p*len(a)))];r={}
  for g in ['B200','H200','H100 SXM','A100 SXM4','L40S','RTX 4090']:
    o=get('https://console.vast.ai/api/v0/bundles/?q='+urllib.parse.quote(json.dumps({'gpu_name':{'eq':g},'rentable':{'eq':True},'type':'on-demand','limit':500})))['offers']
    if o: pg=[x['dph_total']/x['num_gpus'] for x in o]; r[cls(g)]=dict(price=round(st.median(pg),3),offers=len(o),gpus=sum(x['num_gpus'] for x in o),p10=round(q(pg,.1),3),p90=round(q(pg,.9),3))
    time.sleep(.5)
  return r
def runpod():
  d=get('https://api.runpod.io/graphql',data=json.dumps({'query':'{gpuTypes{id displayName securePrice communityPrice secureCloud communityCloud}}'}).encode(),headers={'content-type':'application/json'})['data']['gpuTypes'];r={}
  for x in d:
    c=cls(x['displayName']) or cls(x['id'])
    ps=[p for p,ok in [(x.get('communityPrice'),x.get('communityCloud')),(x.get('securePrice'),x.get('secureCloud'))] if ok and p]
    if c and ps and (c not in r or min(ps)<r[c]['price']): r[c]=dict(price=min(ps),sku=x['displayName'])
  return r
def lambda_():
  h=get('https://lambda.ai/pricing',raw=True).encode().decode('unicode_escape',errors='ignore');t=re.sub(r'\|+','|',re.sub('<[^>]+>','|',h));r={}
  for m in re.finditer(r'\|(NVIDIA [^|]{2,30})\|(\d+) GB\|(\d+)\|[^|]*\|[^|]*\|\$([\d.]+)',t):
    c=cls(m[1]);p=float(m[4])
    if c and (c not in r or p<r[c]['price']): r[c]=dict(price=p,sku=m[1])
  if not r: raise Exception('no rows parsed')
  return r
def aws():
  d=get('https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json')['regions']['US East (N. Virginia)']
  M={'p6-b200.48xlarge':('B200',8),'p5en.48xlarge':('H200',8),'p5e.48xlarge':('H200',8),'p5.48xlarge':('H100',8),'p4de.24xlarge':('A100',8),'g6e.xlarge':('L40S',1)};r={}
  for v in d.values():
    it=v.get('Instance Type')
    if it in M:
      c,n=M[it];p=round(float(v['price'])/n,3)
      if c not in r or p<r[c]['price']: r[c]=dict(price=p,sku=it)
  return r
def azure():
  r={}
  for sku,(c,n) in {'Standard_ND96isr_H100_v5':('H100',8),'Standard_ND96isr_H200_v5':('H200',8),'Standard_ND96amsr_A100_v4':('A100',8)}.items():
    f=urllib.parse.quote(f"armSkuName eq '{sku}' and armRegionName eq 'eastus' and priceType eq 'Consumption'")
    for it in get('https://prices.azure.com/api/retail/prices?$filter='+f)['Items']:
      if 'Windows' in it['productName'] or 'Spot' in it['skuName'] or 'Low Priority' in it['skuName']: continue
      r[c]=dict(price=round(it['retailPrice']/n,3),sku=sku);break
  return r
def openrouter():
  d=get('https://openrouter.ai/api/v1/models')['data'];p=[]
  for m in d:
    try: v=float(m['pricing']['completion'])*1e6
    except Exception: continue
    if v>0: p.append(v)
  return dict(models=len(d),medianOutputUsdPerM=round(st.median(p),3))
KIND={'vast':('Open listings, median of live offers',1.0),'runpod':('GPU cloud list price (lowest tier)',1.0),'lambda':('GPU cloud list price',1.0),'aws':('Hyperscaler on-demand ÷ GPUs (ceiling)',.5),'azure':('Hyperscaler retail ÷ GPUs (ceiling)',.5)}
CAP=.35
def wmedian(pairs):
  pairs=sorted(pairs);tot=sum(w for _,w in pairs);acc=0
  for p,w in pairs:
    acc+=w
    if acc>=tot/2: return p
def capped(ws):
  ws=dict(ws)
  for _ in range(10):
    t=sum(ws.values());ws={k:v/t for k,v in ws.items()};over=[k for k,v in ws.items() if v>CAP+1e-9]
    if not over or len(ws)*CAP<1: break
    free=[k for k in ws if k not in over];ex=sum(ws[k]-CAP for k in over)
    for k in over: ws[k]=CAP
    fs=sum(ws[k] for k in free)
    for k in free: ws[k]+=ex*ws[k]/fs
  return {k:round(v,4) for k,v in ws.items()}
def snapshot():
  raw={};status={}
  for k,fn in [('vast',vast),('runpod',runpod),('lambda',lambda_),('aws',aws),('azure',azure),('openrouter',openrouter)]:
    try: raw[k]=fn();status[k]=dict(ok=True,at=now())
    except Exception as e: status[k]=dict(ok=False,at=now(),error=str(e)[:160])
  idx={}
  for c in CLASSES:
    q={s:raw[s][c]['price'] for s in KIND if s in raw and c in raw[s]}
    if not q: continue
    w=capped({s:KIND[s][1] for s in q});m=wmedian([(q[s],w[s]) for s in q])
    idx[c]=dict(index=round(m,3),quotes=q,weights=w,nSources=len(q))
  return dict(date=TODAY.isoformat(),takenAt=now(),method=f'Weighted median of per-source quotes; hyperscalers weight .5; per-source weight cap {CAP:.0%}',sources=status,raw=raw,index=idx)
def anomalies(snap,prev):
  f=[]
  for c,v in snap['index'].items():
    for s,p in v['quotes'].items():
      if abs(p/v['index']-1)>.5: f.append(dict(cls=c,source=s,type='source_outlier',detail=f'{s} {p} vs median {v["index"]} ({p/v["index"]-1:+.0%})'))
    if prev and c in prev['index']:
      d=v['index']/prev['index'][c]['index']-1
      if abs(d)>.2: f.append(dict(cls=c,type='index_jump',detail=f'{d:+.1%} day-over-day'))
    if v['nSources']<2: f.append(dict(cls=c,type='thin',detail='single source; index not robust'))
  if prev:
    for c,v in snap['raw'].get('vast',{}).items():
      pv=prev['raw'].get('vast',{}).get(c)
      if pv and pv['offers']>=10 and v['offers']<pv['offers']*.5: f.append(dict(cls=c,source='vast',type='supply_drop',detail=f'offers {pv["offers"]}→{v["offers"]}'))
  return f
def canon(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
BT=json.load(open('backtest.json'))
def forecast(snap):
  hist={}
  for f in sorted(glob.glob('data/snapshots/*.json')):
    s=json.load(open(f))
    for c,v in s['index'].items(): hist.setdefault(c,[]).append(v['index'])
  out={}
  for c,v in snap['index'].items():
    x=v['index'];out[c]={}
    for h in H:
      lo,hi=BT['pooledLogBand80'][str(h)];floor={1:.02,7:.04,30:.08}[h]
      if v['nSources']<2: floor*=2
      lo=min(lo,-floor);hi=max(hi,floor)
      ys=hist.get(c,[])[-60:]
      ch=None
      if len(ys)>=14:
        w=ys[::-1][::7][::-1];l,t=w and (math.log(w[0]),0)
        for y in w[1:]: pl=l;l=.5*math.log(y)+.5*(l+.9*t);t=.2*(l-pl)+.8*.9*t
        ch=round(math.exp(l+t*sum(.9**k for k in range(1,max(1,round(h/7))+1))),3)
      out[c][str(h)]=dict(target=(TODAY+dt.timedelta(h)).isoformat(),point=round(x,3),lo80=round(x*math.exp(lo),3),hi80=round(x*math.exp(hi),3),challenger=ch)
  return out
def bsc_commit(digest):
  pk=os.environ.get('BSC_TESTNET_PK')
  if not pk: return None
  try:
    from eth_account import Account
    rpc='https://bsc-testnet-rpc.publicnode.com';call=lambda m,p:get(rpc,data=json.dumps({'jsonrpc':'2.0','id':1,'method':m,'params':p}).encode(),headers={'content-type':'application/json'})
    a=Account.from_key(pk);n=int(call('eth_getTransactionCount',[a.address,'pending'])['result'],16);gp=int(call('eth_gasPrice',[])['result'],16)
    tx=dict(to=a.address,value=0,data='0x'+digest,nonce=n,gas=30000,gasPrice=gp,chainId=97)
    raw=a.sign_transaction(tx).raw_transaction.hex();r=call('eth_sendRawTransaction',['0x'+raw.removeprefix('0x')])
    return dict(chain='bsc-testnet',chainId=97,from_=a.address,tx=r.get('result'),error=r.get('error'))
  except Exception as e: return dict(chain='bsc-testnet',error=str(e)[:200])
def score(snap):
  rows=[]
  for f in sorted(glob.glob('forecasts/*.json')):
    F=json.load(open(f))
    for c,hs in F['forecasts'].items():
      for h,p in hs.items():
        if p['target']!=TODAY.isoformat() or c not in snap['index']: continue
        y=snap['index'][c]['index'];base=F['index'][c]['index']
        sg=lambda z:0 if abs(z)<.01 else (1 if z>0 else -1)
        rows.append(dict(issued=F['date'],target=p['target'],cls=c,h=int(h),point=p['point'],lo80=p['lo80'],hi80=p['hi80'],actual=y,ape=round(abs(y-p['point'])/y,5),inside=p['lo80']<=y<=p['hi80'],
          naiveApe=round(abs(y-base)/y,5),moved=sg(y/base-1)!=0,challenger=p.get('challenger'),challengerDirOk=(sg(p['challenger']/base-1)==sg(y/base-1)) if p.get('challenger') else None,commitSha256=F['_sha256']))
  return rows
def main():
  os.makedirs('data/snapshots',exist_ok=True);os.makedirs('forecasts',exist_ok=True);os.makedirs('scores',exist_ok=True)
  d=TODAY.isoformat();sp=f'data/snapshots/{d}.json'
  if os.path.exists(f'forecasts/{d}.json'): print('already issued',d);return
  prevf=sorted(f for f in glob.glob('data/snapshots/*.json') if f<sp);prev=json.load(open(prevf[-1])) if prevf else None
  snap=snapshot();snap['anomalies']=anomalies(snap,prev)
  json.dump(snap,open(sp,'w'),indent=1)
  # reveal & score anything maturing today (forecasts were committed on earlier days)
  new=score(snap);sc=[json.loads(l) for l in open('scores/scored.jsonl')] if os.path.exists('scores/scored.jsonl') else []
  with open('scores/scored.jsonl','a') as fh:
    for r in new: fh.write(json.dumps(r)+'\n')
  sc+=new
  F=dict(date=d,day=(TODAY-START).days+1,issuedAt=now(),horizonsDays=H,classes=CLASSES,snapshotFile=sp,snapshotSha256=hashlib.sha256(open(sp,'rb').read()).hexdigest(),
    index={c:dict(index=v['index'],nSources=v['nSources']) for c,v in snap['index'].items()},forecasts=forecast(snap),
    model=dict(champion='naive (last index); chosen because it beat damped Holt in the walk-forward backtest',interval='80% band = pooled walk-forward backtest log-error quantiles with floors ±2/4/8% (doubled for single-source classes)',challenger='damped Holt on live weekly index once ≥14 live days exist'))
  body=canon(F);digest=hashlib.sha256(body).hexdigest();open(f'forecasts/{d}.json','wb').write(body)
  ots=None
  try: subprocess.run(['ots','stamp',f'forecasts/{d}.json'],check=True,timeout=120,capture_output=True)
    import base64;open(f'forecasts/{d}.json.ots.b64','w').write(base64.b64encode(open(f'forecasts/{d}.json.ots','rb').read()).decode());os.remove(f'forecasts/{d}.json.ots');ots=f'forecasts/{d}.json.ots.b64'
  except Exception as e: print('ots failed',e)
  bsc=bsc_commit(digest)
  L=dict(date=d,day=F['day'],file=f'forecasts/{d}.json',sha256=digest,issuedAt=F['issuedAt'],openTimestamps=ots,bsc=bsc or dict(chain='bsc-testnet',status='pending: no funded testnet key configured (faucet needs a human)'))
  with open('ledger.jsonl','a') as fh: fh.write(json.dumps(L)+'\n')
  # public scorecard
  led=[json.loads(l) for l in open('ledger.jsonl')]
  agg={}
  for h in H:
    r=[x for x in sc if x['h']==h]
    agg[str(h)]=dict(n=len(r),mape=round(100*st.mean(x['ape'] for x in r),2) if r else None,naiveMape=round(100*st.mean(x['naiveApe'] for x in r),2) if r else None,coverage80=round(100*st.mean(x['inside'] for x in r),1) if r else None)
  json.dump(dict(label='LIVE track record',start=START.isoformat(),day=F['day'],of=30,updatedAt=now(),latest=dict(date=d,index=snap['index'],forecasts=F['forecasts'],anomalies=snap['anomalies'],sources=snap['sources'],openrouter=snap['raw'].get('openrouter')),
    ledger=led,scored=sc[-400:],aggregate=agg,backtest=dict(label=BT['label'],horizons=BT['horizons'],bySeries30d=BT['bySeries30d'],model=BT['model'],baseline=BT['baseline'],sources=BT['sources'])),open('scores/scorecard.json','w'),indent=1)
  print('issued',d,digest)
if __name__=='__main__': main()
