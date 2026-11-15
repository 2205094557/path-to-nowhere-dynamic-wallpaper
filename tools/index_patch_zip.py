# -*- coding: utf-8 -*-
"""索引 PTN 增量补丁 ZIP 中的静态立绘资源。

输入本地 patch ZIP，扫描其 bundle 的 AssetBundle 逻辑名与 Texture2D，输出
patch_resource_index.json。索引记录 ZIP 数据偏移/压缩大小/CRC，运行时可用 HTTP
Range 只下载目标 entry，不必下载整个补丁。
"""
from __future__ import annotations
import argparse,json,sys,zipfile,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def data_offset(zf:zipfile.ZipFile,info:zipfile.ZipInfo)->int:
 with open(zf.filename,'rb') as f:
  f.seek(info.header_offset);h=f.read(30)
  if h[:4]!=b'PK\x03\x04':raise ValueError('bad local header')
  nl=int.from_bytes(h[26:28],'little');el=int.from_bytes(h[28:30],'little')
  return info.header_offset+30+nl+el

def main():
 ap=argparse.ArgumentParser();ap.add_argument('patch');ap.add_argument('--pair',required=True);ap.add_argument('--output',default=str(ROOT/'resources/metadata/patch_resource_index.json'));args=ap.parse_args()
 import UnityPy
 from tools.split_ptn_chunk import read_block
 patch=Path(args.patch);z=zipfile.ZipFile(patch);items={};total=loaded=0
 for info in z.infolist():
  if not info.filename.endswith('.bundle'):continue
  total+=1
  try:
   raw=z.read(info);b=read_block(raw,0);data=b[0] if b else raw;env=UnityPy.load(data);loaded+=1
  except Exception:continue
  ab='';textures=[]
  for o in env.objects:
   try:
    if o.type.name=='AssetBundle':ab=o.read().m_Name or ''
    elif o.type.name=='Texture2D':
     t=o.read();n=t.m_Name or ''
     if n.startswith('lihui_'):textures.append({'name':n,'width':int(t.m_Width),'height':int(t.m_Height)})
   except Exception:continue
  for t in textures:
   prev=items.get(t['name'])
   rec={**t,'patch':args.pair,'entry':info.filename,'asset_bundle':ab,'method':info.compress_type,'crc':info.CRC,'compressed_size':info.compress_size,'file_size':info.file_size,'data_offset':data_offset(z,info)}
   if prev is None or t['width']*t['height']>prev['width']*prev['height']:items[t['name']]=rec
  if total%500==0:print(total,'loaded',loaded,'paintings',len(items),flush=True)
 out=Path(args.output);existing={}
 if out.exists():
  try:existing=json.loads(out.read_text(encoding='utf-8')).get('resources',{})
  except Exception:existing={}
 existing.update(items);payload={'schema_version':1,'cdn_base':'https://xonecn-hotupdatecdn.shziyi.com/release-cn/android/tags/major/2.4/patches','resources':existing}
 out.write_text(json.dumps(payload,ensure_ascii=False,indent=1),encoding='utf-8');print('done bundles',total,'loaded',loaded,'new',len(items),'all',len(existing),'->',out)
if __name__=='__main__':raise SystemExit(main())
