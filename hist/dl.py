import glob,os,urllib.request,concurrent.futures as cf,time
jobs=[]
for f in glob.glob('*.cdx'):
  for l in open(f):
    p=l.split()
    if len(p)<3: continue
    out=f"snap/{f[:-4]}__{p[1]}.html"
    if not os.path.exists(out): jobs.append((f"https://web.archive.org/web/{p[1]}id_/{p[2]}",out))
def g(j):
  for k in range(3):
    try:
      r=urllib.request.urlopen(urllib.request.Request(j[0],headers={'User-Agent':'tidewatch-research'}),timeout=60).read()
      import gzip
      if r[:2]==b'\x1f\x8b': r=gzip.decompress(r)
      open(j[1],'wb').write(r);return 1
    except Exception as e: time.sleep(3)
  return 0
with cf.ThreadPoolExecutor(5) as ex: print(sum(ex.map(g,jobs)),len(jobs))
