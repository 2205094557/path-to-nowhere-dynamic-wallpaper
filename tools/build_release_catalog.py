# -*- coding: utf-8 -*-
"""构建发布用 CDN 资源图鉴。

将 BWiki 官方装束与 library_state 中可按需下载的静态/Live2D 资源合并：
- 名称可匹配的官方装束：回填 painting/type/internal_model；
- 未被 BWiki 收录的游戏资源：追加 source=cdn_catalog 条目；
- 不依赖本机 extracted/local_skins，适合干净发布包。
"""
from __future__ import annotations
import json,re,sys,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];MD=ROOT/'resources/metadata'
try:sys.stdout.reconfigure(encoding='utf-8',errors='replace')
except Exception:pass

def norm(v:str)->str:return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKC',v or '').lower())
def py(v:str)->str:
 try:
  from pypinyin import lazy_pinyin
  return ''.join(lazy_pinyin(v)).lower()
 except ImportError:return ''

def character_match(resource:str,aliases:dict[str,str])->tuple[str,str]|None:
 # lihui_<internal>_<skin> or <internal>_<model id>
 if resource.lower().startswith('lihui_'):
  tail=resource[6:]
 else:tail=resource
 candidates=[]
 for cn,internal in aliases.items():
  for key in {str(internal).lower(),py(cn)}:
   if key and tail.lower().startswith(key+'_'):candidates.append((len(key),cn,tail[len(key)+1:]))
 if not candidates:return None
 _,cn,skin=max(candidates)
 return cn,skin

def main()->int:
 base_path=MD/'skins_bwiki.json'
 if not base_path.exists():
  # 首次迁移：当前 skins.json 仍为干净 BWiki 基线时保存一份权威源
  current=json.load(open(MD/'skins.json',encoding='utf-8'))
  json.dump(current,open(base_path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 base=json.load(open(base_path,encoding='utf-8'))
 skins=base['skins'] if isinstance(base,dict) else base
 skins=[dict(x) for x in skins]
 state=json.load(open(MD/'library_state.json',encoding='utf-8'))
 aliases=json.load(open(MD/'name_aliases.json',encoding='utf-8'))
 profiles=json.load(open(MD/'ptn_render_profiles.json',encoding='utf-8')) if (MD/'ptn_render_profiles.json').exists() else {}
 # 官方表索引：角色 + 装束名拼音/归一键
 official={}
 for s in skins:
  for key in {norm(s.get('name','')),py(s.get('name',''))}:
   if key:official.setdefault((s.get('character',''),key),s)
 used=set();linked=added=0
 for painting in sorted(state.get('static_best',{})):
  hit=character_match(painting,aliases)
  if not hit:continue
  char,skin_key=hit;base_key=re.sub(r'\d+$','',norm(skin_key)) or norm(skin_key)
  target=official.get((char,norm(skin_key))) or official.get((char,base_key))
  locator=state['static_best'][painting]
  loc_fields={'chunk':locator['chunk']} if locator.get('chunk') else {'patch':locator.get('patch'),'entry':locator.get('entry')}
  if target and not target.get('painting'):
   target.update({'painting':painting,'type':'static',**loc_fields});linked+=1;used.add(painting)
  else:
   skins.append({'character':char,'name':skin_key,'bundle':'','painting':painting,'type':'static','series':'','source':'cdn_catalog',**loc_fields});added+=1;used.add(painting)
 # 人物 Live2D：只有能映射到图鉴角色的内部名才发布，NPC/剧情模型不展示
 existing={str(x.get('painting') or '').casefold() for x in skins}
 for model,entry in sorted(state.get('moc3_bundles',{}).items()):
  if model.casefold() not in profiles or model.casefold() in existing:continue
  hit=character_match(model,aliases)
  if not hit:continue
  char,_=hit
  skins.append({'character':char,'name':model,'bundle':'','painting':model.lower(),'type':'live2d','internal_model':model,'series':'','source':'cdn_catalog','chunk':entry['bundle']});added+=1;existing.add(model.casefold())
 out={'schema_version':1,'source_page':base.get('source_page','皮肤图鉴') if isinstance(base,dict) else '皮肤图鉴','skins':skins}
 json.dump(out,open(MD/'skins.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
 print(f'发布图鉴: BWiki {len(base["skins"])}，官方回填 {linked}，CDN补充 {added}，总计 {len(skins)}')
 return 0
if __name__=='__main__':raise SystemExit(main())
