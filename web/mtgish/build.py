#!/usr/bin/env python3
"""Build the pinned native mtgish parser for a browser worker (no upstream edits)."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from paths import ARTIFACTS
from setup_mtgish import REVISION
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'web/client-benchmark/dist/forge/mtgish');p.add_argument('--go',default=shutil.which('go') or 'go');a=p.parse_args()
if not shutil.which(a.go):raise SystemExit('Install Go or pass --go /path/to/go.')
source=ARTIFACTS/'mtgish-source';standard=ARTIFACTS/'mtgish-standard-workflow'
assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()==REVISION
assert not subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip()
if not standard.exists():raise SystemExit('Run python src/setup_mtgish.py first.')
a.out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='mtgish-wasm-') as tmp:
 t=Path(tmp)
 for f in (source/'go_mtg_parser').iterdir():
  if f.name.endswith('_test.go') or f.suffix not in {'.go','.mod'}:continue
  s=f.read_text()
  if f.name=='main.go':s=s.replace('func main() {','func upstreamMain() {',1)
  if f.name=='serialize.go':s=s.replace('os.ReadFile(filename)','browserFiles.ReadFile(filename)')
  if f.name=='prefix_tree.go':
   marker='  allMatchResults := MatchAgainstInner(treeName, 0, 0, debug)'
   assert s.count(marker)==1
   s=s.replace(marker,marker+'\n  browserFarthest = bestTokenCursor // Diagnostic only; matching is unchanged.')
  (t/f.name).write_text(s)
 # Reuse exactly the native adapter's checkOracle, including standard rewrites,
 # per-card names and English -> mtgish -> FULL_CARD validation.
 s=(ROOT/'src/mtgish-adapter.go').read_text()
 s=s[:s.index('\nfunc main() {')]
 for imp in ['"encoding/json"','"bufio"','"path/filepath"','"io"','"strconv"']:s=s.replace('    '+imp+'\n','')
 start=s.index('    file, err := os.CreateTemp(');end=s.index('    if index>len(tokens)',start)
 s=s[:start]+'''    _, _ = TokenReTriesBestMatch(grammar,tree,root,input,false)
    index:=browserFarthest;tokens:=GetTokens(input)
'''+s[end:]
 (t/'adapter.go').write_text(s)
 shutil.copyfile(ROOT/'web/mtgish/bridge.go',t/'bridge.go')
 shutil.copytree(standard/'grammars',t/'grammars',ignore=shutil.ignore_patterns('generate_plurals','*.py','__pycache__'))
 names=set()
 for name in ['oracle.json','dungeons.json','tokens.json','additional_cards.json']:
  for entry in json.loads((standard/'data'/name).read_text()):
   for face in entry.get('cards',[]):
    if face.get('type')!='Stickers':names.add(face['name'])
 (t/'names.json').write_text(json.dumps(sorted(names),ensure_ascii=False))
 env={**os.environ,'GOOS':'js','GOARCH':'wasm','GOMAXPROCS':'2'}
 subprocess.run([a.go,'build','-buildvcs=false','-p','2','-trimpath','-ldflags=-s -w','-o',str(a.out.resolve()/'parser.wasm'),'.'],cwd=t,env=env,check=True)
 goroot=Path(subprocess.check_output([a.go,'env','GOROOT'],text=True).strip())
 runtime=goroot/'lib/wasm/wasm_exec.js'
 if not runtime.exists():runtime=goroot/'misc/wasm/wasm_exec.js'
 shutil.copyfile(runtime,a.out/'wasm_exec.js');shutil.copyfile(source/'LICENSE',a.out/'LICENSE-mtgish.txt');shutil.copyfile(goroot/'LICENSE',a.out/'LICENSE-Go.txt')
 manifest={'upstream':'https://github.com/i5jb/mtgish','revision':REVISION,'go':subprocess.check_output([a.go,'version'],text=True).strip(),'workflow':'upstream-standard-v2','bridge_sha256':hashlib.sha256((ROOT/'web/mtgish/bridge.go').read_bytes()).hexdigest(),'native_adapter_sha256':hashlib.sha256((ROOT/'src/mtgish-adapter.go').read_bytes()).hexdigest(),'patches':['Rename CLI entrypoint','Read grammar from embed.FS','Expose existing farthest-token cursor for diagnostics','Browser callback around native checkOracle'],'grammar_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((t/'grammars').glob('*.json5'))},'files':{f.name:{'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in a.out.iterdir() if f.is_file() and f.name!='manifest.json'}}
 (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({k:manifest[k] for k in ['revision','go','files']},indent=2))
