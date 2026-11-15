# -*- coding: utf-8 -*-
"""构建角色资源目录。

输出 skins.json 中面向 UI 的分类字段：
- portrait / 立绘       <- zhanzi
- promotion / 升阶      <- default
- id_photo / 证件照     <- prisonphoto
- handbook / 档案       <- handbook
- interrogation / 审查  <- trail
- live2d / 动态皮肤     <- live2d_skin_map.json 中已核实映射

尾部 2/3 是缩略/差分，保留在定位索引但不生成用户可见主条目。
"""
from __future__ import annotations
import json,re,sys,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];MD=ROOT/'resources/metadata'
try:sys.stdout.reconfigure(encoding='utf-8',errors='replace')
except Exception:pass

CATEGORIES={
 'zhanzi':('portrait','立绘',10),
 'default':('promotion','升阶',20),
 'prisonphoto':('id_photo','证件照',30),
 'handbook':('handbook','档案',40),
 'trail':('interrogation','审查',50),
}
def py(v):
 try:
  from pypinyin import lazy_pinyin
  return ''.join(lazy_pinyin(v or '')).lower()
 except ImportError:return ''
def norm(v):return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKC',v or '').lower())
def split_resource(resource,aliases):
 tail=resource[6:] if resource.lower().startswith('lihui_') else resource
 candidates=[]
 for cn,internal in aliases.items():
  for key in {str(internal).lower(),py(cn)}:
   if key and tail.lower().startswith(key+'_'):candidates.append((len(key),cn,tail[len(key)+1:]))
 if not candidates:return None
 _,cn,suffix=max(candidates);return cn,suffix

def main():
 base=json.load(open(MD/'skins_bwiki.json',encoding='utf-8'))
 state=json.load(open(MD/'library_state.json',encoding='utf-8'))
 aliases=json.load(open(MD/'name_aliases.json',encoding='utf-8'))
 live_map=json.load(open(MD/'live2d_skin_map.json',encoding='utf-8')) if (MD/'live2d_skin_map.json').exists() else {}
 official=base['skins'];catalog=[];review=[]
 # 五类稳定静态主资源；数字变体不作为独立标签
 per_char={}
 for painting,loc in state.get('static_best',{}).items():
  hit=split_resource(painting,aliases)
  if not hit:continue
  char,suffix=hit
  m=re.fullmatch(r'(default|zhanzi|prisonphoto|handbook|trail)(\d*)',suffix,re.I)
  if not m:continue
  base_key=m.group(1).lower();variant=m.group(2)
  category,label,order=CATEGORIES[base_key]
  rec={'painting':painting,'locator':loc,'variant':variant,'area':int(loc.get('w',0))*int(loc.get('h',0))}
  per_char.setdefault((char,base_key),[]).append(rec)
 for (char,base_key),items in sorted(per_char.items()):
  primary=next((x for x in items if not x['variant']),None) or max(items,key=lambda x:x['area'])
  category,label,order=CATEGORIES[base_key];loc=primary['locator'];locator={'chunk':loc['chunk']} if loc.get('chunk') else {'patch':loc.get('patch'),'entry':loc.get('entry')}
  catalog.append({'character':char,'name':label,'display_label':label,'resource_category':category,'resource_group':'base' if order<40 else 'extra','resource_variant':'main','sort_order':order,'bundle':'','painting':primary['painting'],'type':'static','series':'','source':'resource_catalog',**locator})
 # 已核实动态皮肤映射
 for model,info in sorted(live_map.items()):
  if info.get('status')!='verified':
   review.append({'model':model,**info});continue
  char=info.get('character');skin_name=info.get('skin')
  if not char or not skin_name:continue
  catalog.append({'character':char,'name':skin_name,'display_label':skin_name,'resource_category':'live2d','resource_group':'dynamic','resource_variant':'main','sort_order':100,'bundle':'','painting':model.lower(),'type':'live2d','internal_model':model,'series':info.get('series',''),'source':'live2d_skin_map','evidence':info.get('evidence','')})
 # BWiki metadata 保留在独立基线，不把未映射装束塞进角色资源条
 out={'schema_version':2,'source_page':base.get('source_page','皮肤图鉴'),'skins':catalog,'official_skin_count':len(official)}
 json.dump(out,open(MD/'skins.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
 json.dump({'review':review},open(MD/'live2d_mapping_review.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
 print(f'角色资源目录: {len(catalog)} 条，静态 {sum(x["type"]=="static" for x in catalog)}，动态 {sum(x["type"]=="live2d" for x in catalog)}，待核实动态 {len(review)}')
 return 0
if __name__=='__main__':raise SystemExit(main())
