#!/usr/bin/env python3
"""Regenerate approved named-hero data from the pinned native exporter output.

Inputs are preprocessed campaign configs plus resolved-types.cfg produced by
actual 1.19.26 unit_types (tests/hero_catalog_export.cpp). The supplied game data
is read only. Run in a developer checkout; the Android build uses checked-in
output and does not require Python WML parsing or source campaign loading.
"""
from pathlib import Path
import sys,json,copy,re,hashlib,argparse
parser=argparse.ArgumentParser()
parser.add_argument('--resolved-root',required=True,type=Path)
parser.add_argument('--game-data',required=True,type=Path)
parser.add_argument('--wml-parser-root',required=True,type=Path)
parser.add_argument('--server-repo',type=Path)
args=parser.parse_args()
sys.path.insert(0,str(args.wml_parser_root));import wmlparser3 as w
root=args.resolved_root;client=Path(__file__).resolve().parents[1];game=args.game_data
roster=json.loads((client/'alliance_game/hero_roster_source.json').read_text())['campaigns']
metas=json.loads((root/'campaigns.json').read_text());heroes={};forms={};campaigns=[];wml=[];types_cache={}
def parse(p):
 q=w.Parser();q.parse_binary(p.read_bytes());return q.root
def walk(n):
 yield n
 for x in n.data:
  if isinstance(x,w.TagNode):yield from walk(x)
def text(n,k,d=''):return n.get_text_val(k,d)
def setv(n,k,v):
 n.data=[a for a in n.data if not(isinstance(a,w.AttributeNode) and a.name==k.encode())]
 a=w.AttributeNode(k.encode());a.value=[w.StringNode(str(v).encode())];n.data.append(a)
def clean(node,directory,is_root=True):
 if is_root:
  node.data=[x for x in node.data if not(isinstance(x,w.AttributeNode) and x.name in [b'parent_type',b'export_variation',b'variation_id',b'inherit',b'variation_name'])]
  node.data=[x for x in node.data if not(isinstance(x,w.TagNode) and x.name in [b'advancement',b'variation',b'modifications',b'base_unit'])]
 if node.name==b'event' and not text(node,'id'):
  setv(node,'id','unified_intrinsic_'+hashlib.sha256(node.wml()).hexdigest()[:20])
 for x in node.data:
  if isinstance(x,w.TagNode):clean(x,directory,False)
  elif isinstance(x,w.AttributeNode):
   s=x.get_text()
   def replace(m):
    path=m.group(0)
    for base in ['images','sounds']:
     if (game/'data/campaigns'/directory/base/path).is_file():return 'data/campaigns/'+directory+'/'+base+'/'+path
    return path
   new=re.sub(r'[A-Za-z0-9_./-]+\.(?:png|webp|jpg|ogg|wav)',replace,s)
   if new!=s:x.value=[w.StringNode(new.encode())]
core=parse(root/'core/output/input.cfg');core_races={text(n,'id') for n in walk(core) if n.name==b'race'};added_races=set()
extra={'The_Rise_Of_Wesnoth':{'Prince Haldric':['Noble Youth','Noble Fighter'],'Lady Jessene':['Wesfolk Lady']},'Winds_of_Fate':{'Gorlack':['Drake Burner','Fire Drake','Inferno Drake','Armageddon Drake']},'Descent_Into_Darkness':{'Malin Keshar':['Malin Keshar','Dark Mage'],'Mal Keshar':['Mal Keshar']}}
exclude={'Skiff','Sleeping_Fighter','Unarmed_Fighter','Cage','Cave Bear Poly','Frost Stoat Poly','Giant Crab','Roc'}
for meta in metas:
 directory=meta['directory'];print(directory,flush=True)
 if directory not in types_cache:
  source=parse(root/directory/('output-fixed' if directory=='Heir_To_The_Throne' else 'output')/'input.cfg')
  resolved=parse(root/directory/'resolved-types.cfg');types={text(n,'id'):n for n in resolved.get_all(tag='unit_type')};types.update({text(n,'parent_type')+'|'+text(n,'export_variation'):n for n in resolved.get_all(tag='resolved_variation')});types_cache[directory]=(source,types)
 source,types=types_cache[directory]
 scenario_ids=[text(n,'id') for n in source.get_all(tag='scenario') if text(n,'id')]
 aliases=[]
 for row in roster[directory]:
  alias,hero=row['unit_id'],row['hero_id'];found=[];names=[]
  for n in walk(source):
   if text(n,'id')==alias and n.name in [b'unit',b'side',b'leader',b'modify_unit']:
    if text(n,'type') in types:found.append(text(n,'type'))
    if text(n,'name'):names.append(text(n,'name'))
  found+=extra.get(directory,{}).get(alias,[])
  if not found:print('MISSING',directory,alias);continue
  name=next((n for n in names if '$' not in n),alias)
  if hero=='konrad_ii':name='Konrad II'
  if hero=='ullg':name='Ullg the Flameless'
  heroes.setdefault(hero,{'id':hero,'name':name})
  queue=list(found);seen=set();af=[]
  while queue:
   typ=queue.pop(0)
   if typ in seen or typ in exclude or typ not in types:continue
   seen.add(typ);node=types[typ]
   if '|' not in typ:queue += [k for k in types if k.startswith(typ+'|')]
   source_type,_,source_variation=typ.partition('|')
   if 'sceptre' in source_variation or 'bloodaxe' in source_variation:
    base_id=hero[:25]+'_'+hashlib.sha256((directory+'\0'+source_type).encode()).hexdigest()[:12]
    af.append({'source_type':source_type,'source_variation':source_variation,'form_id':base_id})
    continue
   queue += [v.strip() for v in text(node,'advances_to').split(',')]
   for adv in node.get_all(tag='advancement'):
    for ef in adv.get_all(tag='effect'):
     if text(ef,'apply_to')=='type':queue.append(text(ef,'name'))
   if not text(node,'hitpoints').isdigit() or not text(node,'level').isdigit():print('NOSTATS',typ);continue
   # Same native type in separate story sources stays a separate earned combat form.
   fid=hero[:25]+'_'+hashlib.sha256((directory+'\0'+typ).encode()).hexdigest()[:12]
   af.append({'source_type':source_type,'source_variation':source_variation,'form_id':fid})
   if fid in forms:continue
   faction='Undead' if 'undead' in text(node,'race') else 'Rebels' if 'elf' in text(node,'race') or 'elvish' in text(node,'race') else 'Northerners' if text(node,'race') in ['orc','goblin','troll'] else 'Drakes' if text(node,'race') in ['drake','lizard'] else 'Knalgan Alliance' if text(node,'race') in ['dwarf','gryphon'] else 'Loyalists'
   attacks=[{k:(int(text(a,k,'0')) if k in ['damage','number'] else text(a,k)) for k in ['name','damage','number','type','range']} for a in node.get_all(tag='attack')]
   form={'id':fid,'hero_id':hero,'name':text(node,'name',source_type).split('^')[-1]+((' ('+source_variation+')') if source_variation else ''),'unit_type':'AH_'+fid,'native_level':int(text(node,'level')),'faction':faction,'source_campaign':directory,'source_type':source_type,'source_variation':source_variation,'base_hitpoints':int(text(node,'hitpoints')),'attacks':attacks}
   forms[fid]=form;c=copy.deepcopy(node);c.name=b'unit_type';clean(c,directory);
   race=text(node,'race')
   if race not in core_races:
    rid='AH_'+directory+'_'+race
    setv(c,'race',rid)
    if rid not in added_races:
     rn=next((n for n in walk(source) if n.name==b'race' and text(n,'id')==race),None)
     if rn is None:raise ValueError('Missing custom race '+rid)
     rn=copy.deepcopy(rn);setv(rn,'id',rid);wml.append(rn.wml().decode());added_races.add(rid)
   setv(c,'id',form['unit_type']);setv(c,'name',heroes[hero]['name']+' — '+form['name']);setv(c,'advances_to','null');setv(c,'experience',1000000000);setv(c,'hide_help','yes');setv(c,'do_not_list','yes');wml.append(c.wml().decode())
  aliases.append({'unit_id':alias,'hero_id':hero,'forms':af})
 campaigns.append({**meta,'scenario_ids':sorted(set(scenario_ids)),'aliases':aliases})
cat={'version':'unified-heroes-v1','game_version':'1.19.26','heroes':sorted(heroes.values(),key=lambda x:x['id']),'forms':sorted(forms.values(),key=lambda x:x['id']),'campaigns':campaigns}
raw=json.dumps(cat,ensure_ascii=False,sort_keys=True,indent=2)+'\n';(client/'alliance_game/hero_catalog.json').write_text(raw);
if args.server_repo:(args.server_repo/'alliance/hero_catalog.json').write_text(raw)
wmlraw='\n'.join(wml);(client/'alliance_game/hero_units.cfg').write_text(wmlraw)
(client/'alliance_game/hero_catalog_data.inc').write_text('static constexpr const char catalog_json[] = R"HEROJSON('+raw+')HEROJSON";\nstatic constexpr const char catalog_sha[] = "'+hashlib.sha256(raw.encode()).hexdigest()+'";\n')
(client/'alliance_game/hero_unit_data.inc').write_text('static constexpr const char hero_units_wml[] = R"HEROWML('+wmlraw+')HEROWML";\n')
print('GENERATED',len(heroes),len(forms),len(campaigns),'bytes',len(wmlraw),flush=True)
