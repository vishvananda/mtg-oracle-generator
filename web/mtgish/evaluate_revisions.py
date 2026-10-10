import sys,json,time,urllib.request,subprocess,pathlib
root=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from validate_mtgish import MtgishValidator
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--endpoint',default='http://127.0.0.1:4205/v1/chat/completions');parser.add_argument('--out',type=pathlib.Path,required=True);args=parser.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True)
base=dict(name='Cloudwing Sentinel',rarity='uncommon',mana_cost='{2}{U}',type_line='Creature — Bird',colors=['U'],power='2',toughness='2',oracle_text='Flying\nWhen CARDNAME enters, draw a card.')
cases=[('add-keyword',base,'Give this creature vigilance as well as its existing abilities.',False),('stats',base,'Make it a 3/4. Keep everything else exactly the same.',False),('trigger-amount',base,'Change its enters ability to draw two cards instead of one.',False),('change-name',base,'Rename it Mistwing Scholar. Change nothing else.',False),('repair-draw',{**base,'oracle_text':'Flying\nWhen CARDNAME enters, draws two card.'},'Fix the Oracle wording while preserving its meaning.',True),('repair-loot',{**base,'oracle_text':'Flying\n{T}: Draw one card then discard one card.'},'Fix the Oracle wording while preserving its meaning.',True),('repair-walker',dict(name='Neris, Tide Seeker',rarity='mythic',mana_cost='{2}{U}{U}',type_line='Legendary Planeswalker — Neris',colors=['U'],loyalty='4',oracle_text='+1: Scry two.\n−2: Return target creature to its owners hand.\n−7: Draw seven cards.'),'Fix the Oracle wording while preserving its meaning.',True),('repair-ambiguous',{**base,'oracle_text':'Whenever you cast a spell that is exactly two or more colors, draw a card.'},'Fix the Oracle wording while preserving its meaning.',True)]
system=(root/'web/client-benchmark/dist/system-prompt.txt').read_text();results=[]
with MtgishValidator() as validator:
 for id,card,change,repair in cases:
  before=validator.check(card)
  js="import {revisionMessages} from './web/client-benchmark/forge/revision-prompt.js';let s='';for await(const p of process.stdin)s+=p;const a=JSON.parse(s);console.log(JSON.stringify(revisionMessages(...a)));"
  messages=json.loads(subprocess.check_output(['node','--input-type=module','-e',js],input=json.dumps([system,card,change,before if repair else None]),text=True,cwd=root))
  payload=dict(messages=messages,max_tokens=768,temperature=.2,top_p=.8,top_k=20,min_p=0,seed=42,cache_prompt=True,chat_template_kwargs=dict(enable_thinking=False))
  started=time.monotonic()
  try:
   req=urllib.request.Request(args.endpoint,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
   response=json.load(urllib.request.urlopen(req,timeout=240));raw=response['choices'][0]['message']['content'];draft=json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip());after=validator.check(draft)
   item=dict(id=id,input=card,change=change,repair=repair,before=before,draft=draft,after=after,seconds=round(time.monotonic()-started,2),usage=response.get('usage'),messages=messages)
  except Exception as error:item=dict(id=id,error=str(error),seconds=round(time.monotonic()-started,2))
  results.append(item);print(json.dumps({k:v for k,v in item.items() if k in ['id','draft','seconds','error']}),flush=True)
  args.out.write_text(json.dumps(results,indent=2)+'\n')
