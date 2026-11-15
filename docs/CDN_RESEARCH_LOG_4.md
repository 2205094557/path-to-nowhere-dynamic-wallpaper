# 无期迷途 CDN 分类资源接入软件 + 全链路整合 —— 第四轮（完结轮）

> 日期：2026-11-12
> 承接：`CDN_RESEARCH_LOG_3.md`（slice 通道验证）与 `HANDOFF_CDN_LIVE2D_TEST.md`（test 模型验收）
> 状态：**全链路接入软件并通过 E2E；动画、映射、规范化全部完成**

---

## 0. 结论速览

| 问题 | 本轮答案 |
|---|---|
| CDN 下载的模型动画全不全 | **与本地管线产出完全等价**（奥古斯特 9 动作一一对应、时长逐条一致） |
| 组 ID ↔ 角色怎么对应 | **组 ID = hero_profile.IDNum(6位) + 皮肤序号(2位)**，规律全量验证 |
| 新内容（res v12+）何时可下 | 客户端拉新 manifest → 设备明文清单刷新 → 重拉重建即可，已定位机制 |
| UnityPy 为什么解析失败 | **PTN 容器 = [50B v7 假头][v8 真档]**，剥头即解——本轮最大发现 |
| 其他人能复用吗 | 能；唯一"设备引导"环节是拉 3 个明文清单文件（无需 root） |

---

## 1. 2.5 分支发现（解开"新内容 404"的钥匙）

第三轮结束时新内容 slice 全部 404，原因不是路径规则错，是**游戏已切到 major/2.5**：

```text
2.5/latest/res_version.json = {"Version": 11}   ← res 版本
2.5/classify/0/classify/{slice}                 ← slice 版本字段 = 0
```

- 老内容（2.4/classify/2/...）仍在 CDN 上可下（实测 105,794,560B）
- ⚠️ CDN 对不存在路径返回 **HTTP 200/206 + 3 字节 "404" 文本**，只看状态码必被骗，
  必须检查 Content-Range 总长度

## 2. 设备明文清单：索引不需要解密、不需要 root

客户端更新后把**解密后的清单**写入 `/sdcard/Android/data/com.zy.wqmt.cn/files/`：

| 文件 | 内容 |
|---|---|
| `fsclassify/res_install.json` | 60 个 slice（Filename/FileSize/FileCount/Version） |
| `fs/res_classify.json` | 3585 条有序条目（Name/Hash/Size） |
| `fs/res_base_classify.json` | 303 个分类组 → bundle 列表 |
| `fsclassify/res_base_classify_delete.json` | 用户删除的组（明文！） |
| `fsclassify/res_classify_download_setting.json` | 待下载组列表 |
| `fs/res_manifest` | 23559 条纯路径表（无 chunk 信息，此路不通） |

- 逐条下载**不会**刷新这些文件；只有客户端拉到新 res 版本才重写
- 2.5 全部 31 个补丁扫描确认：**切片清单（res_install/res_base_classify_chunk）不进补丁、
  不在 CDN 公开路径**——只经 RPC 下发，但客户端解密后会落在设备明文里

索引构造：按 slice 顺序用 `res_classify.json` 的有序 Size 逐条累加偏移。
覆盖 3494/3585 条；**Live2d 303 组中 299 组可下载**（4 组 slice 未下发）。

## 3. 组 ID 规律（用户直觉纠正的两条弯路之一）

```text
组 ID = hero_profile.IDNum(6位) + 皮肤序号(2位)
序号即项目命名规则：_2=升阶、_3+=按上线顺序
恩菲尔_3 = 110602 03；克莱门汀_3 = 110411 03（绯夜猎影）
```

- 验证：303 组全部 8 位数字、序号全部 2 位、`110101`(迪蒙) 起 seq=02/03/04 ✓
- `hero_profile_cn.json` 快照只覆盖 136/161 角色；缺的新角色用「下载该组最小 bundle
  （几 KB）→ PtnExtract 读逻辑名 `build_animations2d_characters_<内部名>_<seq>`」认出（18/18）
- 体积交叉验证：组 bundle 之和应等于游戏 UI 显示的「N兆」（绯夜猎影 15.7MB↔"15兆"）
- ⚠️ 序号比较必须 `int()` 归一（`2` vs `02`），此坑咬了两次
- 辅助手段：游戏删组后 `res_base_classify_delete.json` 会明文记录组 ID

## 4. PTN bundle 容器格式（本轮最大发现）

每个 bundle = **[50B UnityFS v7 假头][UnityFS v8 真档]**：

- v7 假头的 compBlocksInfo/uncompBlocksInfo 字段是垃圾（0xFFFFFFFD/FFFFFF…），
  UnityPy/旧版解析器读它会 OverflowError/MemoryError
- **剥头**：`raw[raw.rfind(b'UnityFS'):]` 即 v8 真档，UnityPy 完全正常
- **moc bundle 三层**：[50B 假头][MOC3 明文数据][尾部小 v8 元数据档]
  → `moc3 = raw[MOC3魔数 : 最后一个UnityFS魔数]`
- 头字段：0x48=moc 起点(128)、0x4c=另一边界参考（不精确），终点用 Core 复活校验确认
- Cubism Core 探针三坑：
  ① moc 指针指向缓冲，函数返回后释放 = use-after-free；
  ② 仅 revive 成功不算数（垃圾也能过），必须 InitializeModelInPlace + 参数/部件/drawables 合理；
  ③ 模型工作内存（GetSizeofModel）可小于 1MB（小模型 345KB），阈值别按大模型校准；
  ④ Core 对缓冲对齐敏感 → 多对齐重试（步长 8、0~56）
- **弃用 PTN 工具链**（PtnExtract/PtnLive2DExport）：PtnExtract 会静默跳过 moc 型容器
  （DONE 4 bundles，0 errors，无任何提示），最终装配为纯 UnityPy + sssekai 路线

## 5. moc3 版本分化

| 模型 | 版本 | 本地 Core 校验 | 前端渲染 |
|---|---|---|---|
| Enfer_3 | v3 | 通过（518/131/1020） | ✅ |
| Augustus_2 | v3 | 通过（236/97/860） | ✅ |
| Siglinde_3 | **v5**（Cubism 5） | 本地 Live2DTools DLL 不支持，跳过 | ✅（前端 Core 5.1.0 支持 v5） |

## 6. 动作名规范化（交互/语音逻辑零改动）

CDN 组不含 Fade motion bundle，动画以 AnimationClip 形式存在
（`build_animations2d_characters_*`）。转换复用 `tools/assemble_live2d.py` 既有管线
（sssekai AnimationClip→motion3 + Idle/Background 合并 + Fade 回退），
`tools/assemble_classify.py` 只做剥头喂块。

动作名规范化（`_normalize_motions`）：

```text
clip_{内部名}_{动作} → 本地命名（Background/Boring/Click_Body…，title-case）
IdleCombined         → Idle（官方合并待机为准，替换单层版）
model3.json          → 本地形态（Motions: All=全部 + Idle=Idle）
```

断言结果：standbyLabel、autoLoop、buildInteractSeq、classifyMotion、motionVoiceMap
在 CDN 产物与本地产物行为完全一致——**前端交互/语音逻辑零改动**。

## 7. 奥古斯特对照实验（本地 vs CDN 等价性验证）

- 9 组动作一一对应、时长逐条一致（Boring 12.33s / Click_Hand 22.83s / Greet 11.33s…）
- 曲线数逐条一致（161/198/159/183/203/102/183/29）
- moc3 结构完全一致（params=236/parts=97/drawables=860；2.58MB vs 2.73MB 仅打包差异）
- CDN 版多一条 **IdleCombined**（Idle+Background 官方合并待机）——比本地单层 Idle
  更贴近游戏原生表现（背景层同步动效）
- ⚠️ 待验证：Level 动作时长本地 8.0s vs CDN 4.57s——需游戏内确认哪个是对的
- ⚠️ 待验证：CDN 动作为 30Hz 录制 clip 转换，播放平滑度需实际观感确认
  （游戏本身也是 30Hz 烘焙，理论上应一致）

## 8. 软件接入（E2E 通过）

| 文件 | 内容 |
|---|---|
| `plugins/sources/ptn_cdn.py` | `resolve_classify_target` / `download_classify_group`（Range+MD5+断点跳过） |
| `tools/assemble_classify.py` | 剥头喂块 → `assemble_live2d` 全管线 → 动作名规范化 |
| `tools/assemble_live2d.py` | +`moc3_bytes` 参数、`allow_stub_moc`、tools/ 目录加入 sys.path（sssekai 依赖 `tools/motion3_utils.py`） |
| `backend_server.py` | `download_skin` live2d 分支优先 classify（`_download_skin_classify`，含跳过/取消/SSE/错误落盘） |
| `frontend/src/features/gallery/GalleryView.vue` | 补渲染下载按钮（占位区 + 导出面板两处；`downloadCurrent` 函数一直存在但模板从未渲染） |
| `resources/metadata/classify_index.json` | 组→bundle→slice 偏移（0.65MB） |
| `resources/metadata/classify_group_map.json` | 303 组中文名映射（299 可下） |

E2E：HTTP POST `/api/download` → 5 秒 → 下载+装配+入库+预览渲染（Siglinde_3 158k 像素、
Enfer_5 诡梦回廊、Augustus_2 与本地对照）。

## 9. 覆盖度与遗留

- **299/303 组**可按名字 CDN 下载
- 3 组 review：11031402 / 11061403 / 11061602（未拥有皮肤，slice 未下发，
  设备 bundle 亦无）——等客户端拉 res v12 后重拉清单自动解锁
- 今天新出的皮肤不在删除管理菜单 = manifest 未刷新，同上机制
- 未来工作：manifest 刷新轮询（res_version 11→12+ 自动重拉重建）
- 本地 8.8GB 资产备份于 `D:\codex\_backup_ptn_extracted_live2d`（还原时跳过
  Siglinde_3/Enfer_5 两个已新下载的目录）

## 10. 弯路盘点（全项目，按代价排序）

方向性：
1. 清单加密当成 AES 穷举（字典/哈希/堆扫描全白费）——反编译一眼见 Twofish
2. mitmproxy 抓分类资源（游戏网络层不走系统代理，设了反而失败）——答案在设备明文清单
3. 在 2.4 下找新内容（30+ 路径、补丁链两轮全扫全 404）——`2.5` 存在性一查便知
4. 自研简化组装器而不复用 assemble_live2d 既有管线（动画缺失）——用户纠正
5. 逐组下载认名而不先找组 ID 规律（hero_profile 的 key 就是 IDNum）——用户纠正
6. 对未拥有内容强行找下载路径（从未下发过，任何路径都不存在）

证据类：
7. UnityPy 失败先升级依赖，没先 hexdump 头部——假头垃圾字段一眼可见
8. CDN 探测只看状态码（200/206 + "404" 文本）——必须看 Content-Range
9. 以为清单只有 APK 加密版——设备明文清单（体积不对齐 16 字节的特征）一直都在

环境类：
10. 8766 端口叠多个后端进程（Windows 允许重复绑定）→ 请求轮到旧代码进程
11. Git Bash：路径改写（MSYS_NO_PATHCONV=1）、curl body 按 GBK 发送
12. 序号 `2` vs `02` 未归一（咬了两次）
13. Core 探针 use-after-free / 阈值按大模型校准 / 对齐非确定性
14. MuMu：Frida native 崩溃、tap 自然方向坐标、tcpdump -s 1600 截断 ClientHello
    （详见第二轮日志）

## 11. 可复用性（给其他人）

- 依赖全部公开：UnityPy/sssekai/Pillow（pip）+ Live2DCubismCore.dll（仓库已带）
- 组映射可自举：PtNData 公开数据 + 最小 bundle 认名（公开 GET）
- 唯一设备引导环节：从任意跑着已更新游戏的设备 adb 拉 3 个明文清单（无需 root），
  此后长期纯 CDN
- 合规边界：全程公开 GET、无鉴权绕过；分发范围由项目所有者决定

## 12. 关键脚本索引

```text
tools/assemble_classify.py                  分类装配（剥头+喂块+规范化）
tools/assemble_live2d.py                    既有装配管线（+moc3_bytes/allow_stub_moc）
tools/build_live2d_family_index.py          跨 chunk 家族索引生成（chunk 清单→贴图定位）
tools/export_from_emulator.py               模拟器 adb 一键导出缺失装束（清单→按组拉取→装配）
plugins/sources/ptn_cdn.py                  +classify 通道（解析/下载）+ Range 补取家族块
backend_server.py                           download_skin classify 分支 + 家族补取/规范化
_ptn_cdn_research/watch_download.py         设备文件系统轮询（抓下载瞬间）
_ptn_cdn_research/classify_index_2_5.json   研究版索引（项目版在 resources/metadata/）
_ptn_cdn_research/build_group_map.py        组映射表生成（认名 harvest）
_ptn_cdn_research/make_project_index.py     项目索引生成
_ptn_continue/*                             本轮过程脚本（探针/诊断/对照）
```

## 13. 跨 chunk 的 Live2D 家族（Hameier_5 贴图缺失修复）

**现象**：下载「哈梅尔·依偎涟漪」失败，报 `Live2D 模型 Hameier_5 未提取到 texture_* 贴图`。

**原因**：classify 清单（`res_base_classify.json`，Live2d 289 组）里没有 `11060105`
组，解析回退到 chunk 通道；而该模型的家族**被拆在两个 chunk**：

| 成员 | chunk |
|---|---|
| moc / animation | `res_e0908b2da896c38929de24d64f65f3e9.bin` |
| material / effect / texture（4096，14.8MB） | `res_cffc11b2149b4a7c2b94e25123577ce9.bin` |

旧装配只在下到 moc 的那个 chunk 内找家族成员，贴图必然缺失。同类受影响模型：
`Male_4`、`Male_5`（贴图同样在别的 chunk）。

**清单内 bundle 路径哈希**（反查定位的关键，实测与官方清单一致）：

```text
/assets/<id>.bundle   ←   id = md5(bundleName)[4:20]
```

例：`assets/art_live2d_characters_hameier_5_hameier_5.4096.bundle`
→ `31b618066c567f84`（chunk 13）。贴图 bundle 命名会**重复一次模型名**
（`art_live2d_characters_<m>_<m>.<512|1024|2048|4096>.bundle`），与 moc 的
`art_live2d_characters_<m>.bundle` 不同。

**修复**：

1. `tools/build_live2d_family_index.py` 从解密后的 chunk 清单生成
   `resources/metadata/live2d_family_bundles.json`（只记录不在 moc chunk 的额外
   bundle：chunk/offset/length）；
2. `PtnCdnSource.download_chunk_range()` 用 HTTP Range 精确取单个块（含 50B 前缀），
   校验长度（CDN 对不存在路径返回 200/206 + 三字节 "404"）；
3. `assemble_from_chunk(..., extra_blocks=...)` 把补取块接在 chunk 块流之后，
   两遍扫描/装配逻辑不变；
4. `backend_server.download_skin` 的 live2d chunk 分支先补取家族块再装配，并统一
   执行 `normalize_motions`（`clip_*` → `Idle/Background/Click_*`），与 classify
   通道命名一致。

**重建索引**（版本更新后需要新的解密清单）：

```text
python tools/build_live2d_family_index.py <解密后的 res_chunk_manifest.json>
```

验收：Hameier_5 装配出 1 张 4096² 贴图 + 12 条动作（Idle/Background/Boring/
Click_*/Exit/Greet/Level/Prepare），与本地历史产物命名一一对应。

### 13.1 清单缺组：资源根本不在这两份离线清单里

Hameier_5 虽然 classify 缺组，但 moc/贴图在基础 chunk 中，故可下。另有一批装束
**classify 映射与基础 chunk 都没有 moc/贴图**（基础 chunk 里只有 `_animation`），
因此任何通道都定位不到，会报 `Live2D 模型未在 library_state.moc3_bundles 中找到`：

| 装束 | painting | 应有组 ID |
|---|---|---|
| 可可莉克·绯夜初醒 | `coquelicot_5` | 11060405 |
| 瑟琳·微明止息 | `shalom_6` | 11040506 |
| 卓娅·荒雪统御 | `zoya_5` | 11020105 |
| 诺克斯·画中晨曦 | `nuokesi_5` | 11020205 |
| 克里斯蒂娜·快车晨旅 | `christina_3` | 11120803 |
| 莓丝·无奇典当 | `mess_3` | 11120603 |

前 5 款同属「心隅密约系列」；整套系列在 `res_base_classify.json`(Live2d 289 组) 与
`classify_index.json`(303 组) 中都没有组，也不是 APK/补丁可解的加密清单问题。
处理方式只有刷新设备清单：

1. 在设备/模拟器上更新游戏，进入资源下载管理，让客户端拉到新 manifest
   （或直接下载该装束）；
2. 拉取 `fs/res_base_classify.json`、`fs/res_classify.json`、
   `fsclassify/res_install.json` 三份明文清单；
3. 重新生成 `classify_index.json` / `classify_group_map.json` 后，
   `resolve_classify_target` 即可命中。

在此之前本地已有完整产物的装束（如 `Coquelicot_5`）可直接从项目资产备份
`_backup_ptn_extracted_live2d` 恢复：拷 `*.moc3` 为 `model.moc3`、贴图/动作按
`textures/`、`motions/` 放好，`model3.json` 的 `Moc` 指向 `model.moc3`，
再跑 `build_local_index` + `link_skins` 即完成挂接。

## 14. 模拟器一键导出（tools/export_from_emulator.py）

**目标**：不搬 17GB 全量，只把「本地缺失、且设备清单里可定位」的装束导出。

```text
连 adb（MuMu 默认 D:/Program Files/Netease/MuMu/nx_main/adb.exe）
  → 拉清单（fs/res_base_classify.json、fs/res_classify.json、fsclassify/res_install.json 等，几 MB）
  → 算目标：skins.json 中 type=live2d 且本地无 model3.json
     组 ID 依次尝试：classify_group_map → hero_profile IDNum → 映射表同角色 IDNum 反推
  → 用切片清单判断该组是否已下发（未下发直接给出原因，不做无效拉取）
  → 只拉该组 bundle（fsclassify/assets/xx/hash.bundle，通常 5 个）
  → assemble_from_group 装配 + 动作名规范化
  → 可选重建本地索引，并把新组合并进 classify_index / classify_group_map
```

用法：

```text
python -I tools/export_from_emulator.py --dry-run          # 体检：哪些能导、哪些不能及原因
python -I tools/export_from_emulator.py --limit 10         # 只导最新 10 个缺失装束
python -I tools/export_from_emulator.py --painting coquelicot_5 hameier_5
python -I tools/export_from_emulator.py --refresh-index    # 同时刷新 classify 索引/组映射
python -I tools/export_from_emulator.py --device-files D:/dump/files   # 不连 adb，扫已拷目录
```

注意：

- 设备清单必须是**明文**（游戏完成一次资源更新后会写 `/sdcard/Android/data/com.zy.wqmt.cn/files/`）；
  密文会在解析前直接报错提示。
- 「不可用」的典型原因：组不在设备清单（客户端未下发）、切片未下发、组在待下载列表。
  这些只能先在游戏内下载/拥有该装束，工具无法凭空变出资源。
- 本地已存在 `model3.json` 的装束默认跳过；要重导用 `--all`。

### 14.1 预览抓取模式（--watch）

**为什么「游戏里能预览、清单里却没有」**：购买界面的预览走服务端按需下载，组 ID 只写在
`res_classify_download_setting.json`（待下载列表），下载完也不会回写
`res_base_classify.json` 的资源目录。所以离线索引里查不到，但客户端确实能把文件拉下来。

抓取流程：先停在能点预览的界面，运行命令后立刻点预览；脚本轮询
`fsclassify/` 与 `cache/` 的文件列表，发现新落盘的 bundle 就拉回本地，按 AssetBundle
家族分组（贴图名重复模型名会自动归并），再复用装配管线导出。

```text
python -I tools/export_from_emulator.py --watch --watch-timeout 300
```

注意：若预览完全流式、不落盘，则抓不到；此时改用游戏内「资源管理」把该组下载完，
再跑 `--dry-run` 确认它进入资源目录后用常规模式导出。

### 14.2 扫描目录模式（--scan-dir）

不依赖任何清单/组 ID，直接扫游戏目录或资产 dump 里的 `*.bundle`：读 AssetBundle 名和
moc 模型名，按家族分组后装配。适用于「游戏里明明有、清单里查不到」的资源（游戏内按需
下载后落盘的分类资源）。

```text
python -I tools/export_from_emulator.py --scan-dir D:/ptn_dump/fsclassify --dry-run
python -I tools/export_from_emulator.py --scan-dir D:/ptn_dump/fsclassify --painting coquelicot_5
```

验证（2026-11-12）：用 `dl_char_11041103`（克莱门汀·绯夜猎影，真实 bundle）跑扫描装配，
与既有 classify 产物逐项一致——moc3 4,405,952B、36 部件/240 参数、贴图 MD5 相同、动作集合相同。

## 15. 2026-11-11 新容器格式与资源定位方法（第五轮）

**背景**：11-11 游戏更新后，基础资源整体换新——设备 `fschunk` 下 75 个 chunk 与旧索引的 63 个
完全不重名，容器格式也变了。旧解析器与旧清单全部失效。本轮把新格式、资源定位方法和两个真实
模型（莎缇拉 / 瑟琳·微明止息）全部跑通。

### 15.1 两种容器格式

| | 旧版（≤2026-11-10） | 新版（2026-11-11 起） |
|---|---|---|
| 块头 | 50B 前缀 + XOR 头 | 50B **明文**头 |
| 内容 | XOR 的 UnityFS v7 (2019.4.40f1) | 明文 UnityFS **v8** (2022.3.62f3) |
| 块间距 | 紧接 | **64 字节对齐** |
| 混排 | 否 | 是（部分块无 50B 前缀，直接裸 UnityFS） |

解析规则（实现在 `tools/export_from_emulator.py::iter_chunk_bodies`）：

- `raw[off:off+7] == b"UnityFS"` 时：
  - 若 `raw[off+50:off+57] == b"UnityFS"`：带前缀块，`body = off+50`，`fs = u64be(body+30)`；
  - 否则：裸块，`body = off`，`fs = u64be(off+30)`；
  - 下一块 = `align64(body + fs)`。
- 否则回退旧格式 `split_ptn_chunk.read_block`（XOR 密钥 = `raw[off+50] ^ 0x55`，校验 `raw[off+51] ^ 0x6E`）。
- 社区脚本 lylx1997/PathToNoWhere-Decrypt-and-Split 用 `cbi/ubi != 0xFFFFFFFF` 判定「真 bundle」，
  与实测一致；其 64 字节对齐逻辑也与本项目实现相互印证。

### 15.2 资源定位：以内容扫描为准，不信清单与文件名

- `classify_group_map.json` / `classify_index.json` **缺组 ≠ 文件不存在**：清单只代表客户端已登记分组。
- 新容器里 AssetBundle 名常被压缩，**明文 grep 的负结果不可信**。
- 正确做法：用 UnityPy 逐个打开 bundle / chunk 块，读 `AssetBundle.m_Name` 与 moc 的
  `MonoBehaviour.m_Name`。本次据此找到 `shalom_6` 的 moc（`1df007d7e34e6a51.bundle`），
  而之前 grep 完全没命中。
- 宿主机可直接读模拟器共享目录 `D:/文档/MuMu共享文件夹/files`（`fs` / `fsclassify` / `fschunk`，
  实测约 15GB / 75 chunk / 3580+ bundle），优先于 adb。
- 设备 chunk 集必须以**当前目录**为准，旧 `res_chunk_manifest` 与 63-chunk 索引一律作废。

### 15.3 装配链路上发现并修复的 bug

1. `tools/assemble_classify.py`：
   `body = raw[i_v8:] if i_v8 > 0 else None` 会跳过从 0 字节开始的裸 UnityFS 块，导致
   「未提取到 texture_* 贴图」。改为 `i_v8 >= 0`。
2. moc 与贴图跨构建混用会导致渲染碎片化。实测两套 `shalom_601` 贴图 **78.9% 像素不同**；
   必须使用同一目录/同一构建的 moc + 贴图 + 动画装配。

### 15.4 第三方提取工具为何失效

`Live2DTools/无期迷途Live2D提取工具.exe` 的解包核心是 `PtnExtract.dll` + `AssetStudio.dll`
（10-17 构建），不解新版 UnityFS v8 / 新容器；表现是：**能枚举模型名，但 moc/fade 全部失败**
（报告里 `moc:0, fade:0`，`texture:1`），最终「处理未完成」。8.8GB 备份是 11-10（旧格式）做的，
所以当时完整。

### 15.5 修复后的验证

- `Shatila_2`：组 11061602，5 个 bundle；moc 3.69MB（99 部件 / 221 参数）、9 动作、4096 贴图；
  与用户手动导出贴图逐像素最大差 1/255（同一图像，仅编码舍入）。
- `Shalom_6`：moc 7.62MB（124 部件 / 463 参数）、12 动作、2×4096 图集。
- `Shalom_601`：moc 35.86MB（106 部件 / 317 参数）、8 动作、4096 贴图。
- 三者均在前端实际渲染验收通过。

## 16. 四星皮的两阶段（独立双包）

命名规律（实测）：

- 阶段一：`<internal>_<seq>`，如 `shalom_6`、`coquelicot_5`；
- 阶段二：`<internal>_<seq>01`，如 `shalom_601`、`coquelicot_501`；
- 两者是**独立包**：各自独立的 moc、贴图、动作，不是同一包内的图集拆分。

| 皮肤 | 阶段一 | 阶段二 | 现状 |
|---|---|---|---|
| 瑟琳·微明止息 | `shalom_6`：7.62MB moc / 12 动作 / 2×4096 | `shalom_601`：35.86MB moc / 8 动作 / 4096 | 两阶段均已提取并渲染通过 |
| 可可莉克·绯夜初醒 | `coquelicot_5`：3.97MB moc / 双 4096 图集 | `coquelicot_501`：4.35MB moc / 143KB 动作；贴图缺失 | 阶段二仅缺贴图 |

注意：

- 同一包内的多张贴图是**图集拆分**（如 `coquelicot_5` 的 `texture_00` 是场景/帘幕、
  `texture_01` 是人物/服饰），必须同时加载，不能当成两个阶段；单挂一张会导致整模型黑屏。
- 阶段二的贴图与阶段一**不通用**：用 501 的 moc 配 5 的贴图实测渲染碎片化。
- 图鉴命名统一为「装束名 · 阶段一 / 阶段二」；可可莉克阶段二的贴图经 15GB 全量内容扫描 +
  75 个 chunk 逐块扫描确认不存在，需客户端完整加载该阶段后再提取。

## 17. classify 直链下载（2026-11-12，第六轮）

**结论**：classify 资源可以按清单条目直接拼 URL 下载，不需要 slice 名，也不需要计算片内偏移。

```text
https://xonecn-hotupdatecdn.shziyi.com/release-cn/android/tags/major/2.5/
classify/{entry.Version}/classify/{entry.Name}     # Name 去掉前导 "/"
```

关键点：

- **目录里的版本号是每条清单自己的 `Version`**，不是全局 `slice_version`（全局是 0）。
  用固定 `classify/0` 会让大量条目返回「假 404」（实测莎缇拉的 moc/贴图都在 `classify/5`）。
- 清单来源：设备 `fsclassify/res_base_classify_download.json`（明文，Generation 3，3580 条，
  字段 `Name` / `Hash` / `Size` / `Version`）。项目内固化到
  `resources/metadata/classify_download_manifest.json`。
- 下载仍校验长度与 MD5；直链条目失败时回退旧的 slice + Range 逻辑。

实测验收（纯 CDN、不依赖设备在线）：

```text
组 11061602（莎缇拉）：5/5 bundle 直链下载成功
  bc6e981c77310880.bundle  3,693,509  Version 5
  4eaf2e8effa12fbb.bundle     98,007  Version 5
  0b856f6bf378ed16.bundle 15,738,681  Version 5
  84d56401396b1d15.bundle      5,232  Version 6
  2f400009dc553958.bundle      9,067  Version 5
装配后 model.moc3 / texture_00.png 的 MD5 与设备提取版完全一致。
```

抽样 20 条清单条目，17 条直链可用；不可用条目（如 `coquelicot_5/501` 的贴图）不在
classify 清单里，属于基础 chunk 或尚未下发，直链也无解。

**边界**：

- 清单只覆盖 classify 资源；`fschunk` 基础 chunk 里的模型不在其中。
- 清单必须由设备生成一次（CDN 上不存在该清单文件，实测多种路径均为假 404）。
- 2.5 分支的新 chunk 可整包下载（`major/2.5/chunk/res_xxx.bin`）；2.4 分支对同一文件返回假 404。

## 18. 设备本地扫描装配（2026-11-12，第七轮；瑟琳·微明止息两阶段完整复活）

**适用场景**：装束不在 classify 303 组内（如 `shalom_6` / `shalom_601`），直链与 slice 两条
CDN 通道都取不到，但设备上**已经有资源**。此时唯一可靠路径是扫描模拟器共享目录。

### 18.1 设备三个资源目录分工

宿主直接可读：`D:/文档/MuMu共享文件夹/files`（无需 adb）

| 目录 | 内容 | 命中 shalom_6？ |
|---|---|---|
| `fs/assets/<xx>/<hash>.bundle` | 明文 UnityFS 资产（moc / 贴图 / prefab / 动画） | **是** |
| `fschunk/*.bin` | 基础 chunk（75 个） | 相关但不含模型主体 |
| `fsclassify/assets/<xx>/<hash>.bundle` | classify 通道资源 | **否**（只有 shalom_2/3/4/5） |

> 先前的误判是把搜索范围限制在 `fsclassify/assets`，得出「设备上没有 shalom_6」的错误结论。
> **三个目录都要搜**，且必须用内容级搜索而非文件名。

### 18.2 定位：二进制 grep + 剥头校验（实测 1.4 秒出结果）

```bash
cd "D:/文档/MuMu共享文件夹/files" && grep -rlia "shalom_6" fs/assets
```

拿到哈希名列表后，逐个剥掉 50B 前缀读出 `AssetBundle.m_Name` 确认身份
（`iter_chunk_bodies` 同时兼容 `[50B+XOR v7]` 与 `[50B 明文+v8]`）。

实测瑟琳·微明止息的 8 个 bundle：

| bundle | 大小 | 内容 | UnityFS flags |
|---|---|---|---|
| `1d/1df007d7e34e6a51` | 7,623,380 | `art_live2d_characters_shalom_6_shalom_6`（moc 容器） | `0x3343`（**UnityCN 加密标志位 0x3000**） |
| `83/83770c8c725889ed` | 22,191,120 | `..._shalom_6.4096`（**Texture2D ×2**） | `0x43`（可正常 UnityPy 读取） |
| `9d/9d5e826e5eceb0e2` | 35,860,549 | `..._shalom_601`（moc 容器） | `0x3343` |
| `97/977dff7eb5ff4049` | 16,422,353 | `..._shalom_601.4096`（Texture2D ×1） | 正常 |
| `8c/8ce6be5c71aba6be` | 9,587 | `char2d_shalom_601` prefab（含 Material ×10） | 正常 |
| `fb/fb49ec6562996786` | 298,903 | `..._shalom_601_animation`（AnimationClip ×8） | 正常 |

⚠️ moc 容器带 `0x3000` 位，UnityPy 报 `BundleFile is encrypted, but no key was provided`，
`read_block` 的 XOR 校验也会失败——**这是加密标志，不是 XOR 格式**。直接按字节切片即可。

### 18.3 moc 切片边界（复核两次，稳定）

```text
MOC3 魔数位于 offset 128（紧跟 50B 前缀 + 头）
真实数据 = raw[128 : raw.rfind(b"UnityFS")]
shalom_6   → 7,621,824 字节
shalom_601 → 35,859,008 字节
```

与项目里既有 `model.moc3` 的字节数**完全一致**，因此 moc 本身从来没问题。

### 18.4 一键装配（官方入口，勿手写）

```bash
python -I tools/export_from_emulator.py   --device-files "D:/文档/MuMu共享文件夹/files"   --scan-dir   "D:/文档/MuMu共享文件夹/files"   --painting shalom_6 shalom_601 --force
```

实测输出：

```text
[扫描] 4118 个 bundle
[扫描] 识别出 2 个含 moc 的 Live2D 家族
  - shalom_6（3 个文件）
  - shalom_601（3 个文件）
OK  shalom_6: 贴图 2 张 / 动作 12 条
OK  shalom_601: 贴图 1 张 / 动作 8 条
[索引] 本地索引与图鉴挂接已重建
```

**通道判定（重要）**：同一条命令换成不带 `--scan-dir` 的 classify 模式，会输出
`暂不可用：设备清单无该组`——因为 shalom_6/601 根本不在 classify 303 组里。
报「无该组」不等于设备没资源，**要换扫描通道**。

### 18.5 本轮踩过的四个坑

1. **目录在分析途中被清空**：`resources/extracted/live2d/Shalom_6` 在会话中途消失，
   导致后续所有"贴图异常"判断都建立在残缺目录上。教训：动手前先 `ls` 确认目录还在，
   结论要能复现。
2. **贴图不要用 `_clean_textures` 的产物做根因判断**：清理只把 `alpha==0` 的 RGB 置零，
   PNG 体积会显著变小（17.2MB→12.8MB），但可见内容不变。怀疑贴图时必须以原始导出为准。
3. **35.8MB 的 moc 不是异常**：曾按"moc 不应大于贴图"推断 601 损坏，属误判；
   该阶段本就是大模型（场景级）。判定标准应是 `MOC3` 魔数 + 尾部 `UnityFS` 位置。
4. **前端"未下载"与资源无关**：`local_skins.json` 已含 shalom_6 时，页面仍显示
   「未下载」的根因是**多套 dev 进程并存**——其中一个实例的元数据请求落到
   `/resources/metadata/local_skins.json`（返回 HTML 404），而不是 `/@fs/...`。
   处理：`python -I scripts/start_dev.py --stop`（本次清掉 6 个进程）后重启。

### 18.6 验收口径

- `resources/extracted/live2d/<Model>/model3.json` 的 `Moc` / `Textures` / `Motions`
  引用必须全部存在（写脚本校验，别只看目录非空）。
- `resources/metadata/local_skins.json` 必须出现该 painting 且 `asset.dir` 指向该目录。
- 前端 `currentSkin.status` 应为 `downloaded`，且 `window.__L2D_MODEL__.internalModel.settings.url`
  指向目标 `model3.json`（不是上一个模型的 URL——切皮肤失败时会静默停留在旧模型）。
