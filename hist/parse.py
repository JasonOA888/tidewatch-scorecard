import re,html,glob,json,collections
G={'H100':r'H100(?! ?NVL)','H200':r'H200(?! ?NVL)','B200':r'B200','A100':r'A100(?![^|]{0,8}40 ?GB)','L40S':r'L40S','RTX 4090':r'(?:RTX )?4090'}
def clean(t):
  t=re.sub(r'<(script|style)[^>]*>.*?</\1>',' ',t,flags=re.S|re.I);t=html.unescape(re.sub('<[^>]+>','|',t));return re.sub(r'\s*\|[\s|]*','|',t)
out=collections.defaultdict(dict)
for f in sorted(glob.glob('snap/*.html')):
  src,ts=f[5:-5].split('__');src={'runpod.io_pricing':'runpod'}.get(src,'lambda' if 'lambda' in src else src)
  if src=='vast.ai_pricing':continue
  t=clean(open(f,errors='ignore').read());d=ts[:4]+'-'+ts[4:6]+'-'+ts[6:8]
  for g,rx in G.items():
    ps=[]
    for m in re.finditer(rx,t):
      seg=t[m.end():m.end()+220]
      if re.search(r'H100|H200|B200|A100|L40S|4090',seg[:120].split('$')[0][3:]) : continue
      p=re.search(r'\$\|?\s*(\d{1,2}\.\d{2})',seg)
      if p and 0.15<float(p[1])<15: ps.append(float(p[1]))
    if ps: out[(src,g)][d]=min(ps)
json.dump({f'{a}|{b}':v for (a,b),v in out.items()},open('series.json','w'),indent=0)
for k,v in sorted(out.items()):
  ks=sorted(v);print(k,len(ks),ks[0],ks[-1],[v[x] for x in ks[::max(1,len(ks)//12)]])
