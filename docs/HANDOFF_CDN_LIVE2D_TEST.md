# PTN 分类 Live2D CDN 下载与 `test` 模型验证——AI 迁移交接

> 更新日期：2026-11-11  
> 项目根目录：`D:\codex\path-to-nowhere-dynamic-wallpaper`  
> 研究工作区：`D:\codex\_ptn_cdn_research`  
> 当前状态：**官方 CDN 分类资源下载、精确 Range 取件、Live2D 导出、Cubism Core 验证，以及软件 UI 实际显示验收全部通过（2026-11-11，用户确认渲染正确）。**

---

## 1. 给下一位 AI 的一句话介绍

我们已经逆向并实证跑通：

```text
官方加密分类清单
→ Twofish 解密
→ Live2D group 对应 bundle 列表
→ classify .slice 精确偏移
→ 官方 CDN HTTP Range 只下载目标角色的 bundle
→ MD5 逐个校验
→ PtnExtract 解包
→ PtnLive2DExport 导出 MOC3 / 贴图 / 动作
→ Cubism Core 成功加载
→ 组装成项目模型 test 并写入 local_skins.json
```

下一步不要再研究清单算法或 CDN URL，直接继续：**重启开发后端让它重载 `local_skins.json`，在软件中搜索并预览 `test`，进行 UI/渲染验收。**

---

## 2. 已经实证完成的能力

### 2.1 清单解密

此前猜测 AES 是错误路线，实际为：

```text
Twofish-128
BouncyCastle TwofishEngine
PaddedBufferedBlockCipher
PKCS7 padding
```

默认密钥：

```text
D(G+KbPeShVmYq3t
```

来源：

```text
Framework.KernelConst.DefaultKey
= Encoding.UTF8.GetBytes("D(G+KbPeShVmYq3t")
```

反编译调用链：

```text
FileSystemExtensions.ReadSecretJson
→ ReadSecretText
→ ReadSecretBytes
→ PaddedBufferedBlockCipher(TwofishEngine)
```

成功解密过：

```text
res_classify.json
res_base_classify.json
res_base_classify_chunk.json
res_chunk_manifest.json
res_lite_chunk_manifest.json
res_base.json
res_base_classify_download.json
res_install.json
```

实现：

- `D:\codex\_ptn_cdn_research\DecryptSecret.java`
- `D:\codex\_ptn_cdn_research\bcprov.jar`
- `D:\codex\_ptn_cdn_research\jdk25\`

命令：

```powershell
java -cp ".;bcprov.jar" DecryptSecret `
  "D(G+KbPeShVmYq3t" `
  encrypted.json `
  decrypted.json
```

### 2.2 分类切片 CDN URL

反编译 `CollectChunks`（RVA `0x365DC68`）得到：

```c
String.Format("{0}/{1}/classify/{2}", remoteRelativePath, Version, Filename)
```

其中 `remoteRelativePath = "classify"`。

完整 URL：

```text
https://xonecn-hotupdatecdn.shziyi.com/
release-cn/android/tags/major/2.4/
classify/{Version}/classify/{Filename}
```

实例：

```text
https://xonecn-hotupdatecdn.shziyi.com/release-cn/android/tags/major/2.4/classify/2/classify/res_830046dbe3ec7dffc606d51b240b0389.slice
```

验证：

```text
HTTP: 200/206
文件大小: 105,794,560 B
MD5: 830046dbe3ec7dffc606d51b240b0389
与清单完全一致
```

全程只使用公开 GET / Range，无鉴权绕过。

### 2.3 `.slice` 容器格式

每个切片由有序条目拼接：

```text
[512B 条目头][payload][padding 到 512 对齐]
```

定位公式：

```python
entry_start_0 = 0
payload_offset = entry_start + 512
entry_total = roundup(512 + Size, 512)
next_entry_start = entry_start + entry_total
```

条目头包含：

```text
assets/xx/xxxxxxxxxxxxxxxx.bundle
定宽数字字段
零填充
```

本地完整切片对 `BaseArtManifest` 前 88 条逐条验证：

```text
路径、Size、payload MD5：88/88 全部通过
```

验证脚本：

- `D:\codex\_ptn_cdn_research\verify_layout3.py`
- `D:\codex\_ptn_cdn_research\dump_hdr.py`

### 2.4 按 Live2D 组精确下载

脚本：

```text
D:\codex\_ptn_cdn_research\dl_by_character.py
```

验证组：

```text
Live2d group: 11060203
内部模型名: Enfer_3
角色: 恩菲尔
```

结果：

```text
9 个 bundle
约 23.13 MiB
全部使用 HTTP Range，仅取目标字节
9/9 MD5 与同源官方清单一致
```

下载原始产物：

```text
D:\codex\_ptn_cdn_research\dl_char_11060203\
```

完整日志：

```text
D:\codex\_ptn_cdn_research\dl_char_out.txt
```

重要：定位、Size、Hash 必须取自**同一份** `res_base_classify_chunk.json`。曾经出现 7/9，是因为把当前版 `res_classify.json` 的 Hash 与 APK 内置旧切片混用；改成同源后为 9/9。

---

## 3. 已证明下载到的是动态立绘

用户提供的开源工具位于：

```text
D:\codex\path-to-nowhere-dynamic-wallpaper\tools\ptn_extractor\tools_clean\Live2DTools
```

核心组件：

```text
_internal\unpacker\PtnExtract.exe
_internal\rawexport\PtnLive2DExport.exe
_internal\engine\converter_reference.py
_internal\engine\Live2DCubismCore.dll
```

### 3.1 D 盘隔离验证目录

所有输入、输出和临时目录都显式放在 D 盘：

```text
D:\codex\_ptn_cdn_research\verify_work\
├─ game\
├─ out\
├─ export\
└─ tmp\
```

运行时曾设置：

```text
TMP=D:\codex\_ptn_cdn_research\verify_work\tmp
TEMP=D:\codex\_ptn_cdn_research\verify_work\tmp
```

没有把大量资源解压到 C 盘。

### 3.2 PtnExtract 验证

执行形式：

```powershell
PtnExtract.exe <game-dir> <out-dir>
```

结果：

```text
DONE 9 bundles / 0 errors / 11 files / 0 conflicts
```

解包输出：

```text
D:\codex\_ptn_cdn_research\verify_work\out\
├─ Unity\CAB-*
├─ summary.json
├─ 资源索引.jsonl
└─ 提取错误.jsonl
```

### 3.3 PtnLive2DExport 验证

命令形式：

```powershell
PtnLive2DExport.exe out\资源索引.jsonl export enfer
```

结果：

```json
{
  "selected": 9,
  "moc": 1,
  "fade": 11,
  "texture": 2,
  "meta": 2,
  "errors": []
}
```

导出的逻辑 bundle 名：

```text
assets/art_live2d_characters_enfer_3.bundle
assets/art_live2d_characters_enfer_3_effect.bundle
assets/art_live2d_characters_enfer_3_enfer_3.2048.bundle
assets/art_live2d_characters_enfer_3_enfer_3.4096.bundle
assets/art_live2d_characters_enfer_3_animation.bundle
assets/art_live2d_characters_enfer_3_motion.bundle
assets/art_live2d_characters_enfer_3_material.bundle
assets/build_prefabs_live2d_characters_char2d_enfer_3.bundle
assets/build_animations2d_characters_enfer_3.bundle
```

说明这是 `Enfer_3` 的完整动态 Live2D 家族：主模型、双分辨率贴图、动作、材质、Prefab、交互时间轴。

### 3.4 MOC3 与 Cubism Core 验证

还原文件：

```text
D:\codex\_ptn_cdn_research\verify_work\export\Enfer_3.moc3
```

验证结果：

```text
大小：3,261,952 B
magic：MOC3
csmReviveMocInPlace：成功
csmInitializeModelInPlace：成功
Parameters：518
Parts：131
Drawables：1020
```

贴图：

```text
4096×4096 RGBA
2048×2048 RGBA
```

动作：

```text
Background
Boring
Click_Body
Click_Hand
Click_Head
Click_Leg
Click_Leg2
Greet
Idle
Level
```

因此已经确认：下载到的确实是可加载的动态立绘模型，不是静态素材。

---

## 4. 已组装进软件的 `test` 模型

用户要求把该模型命名为 `test` 并放入软件。

正式目录：

```text
D:\codex\path-to-nowhere-dynamic-wallpaper\resources\extracted\live2d\test\
```

当前文件：

```text
test\
├─ test.moc3
├─ test.model3.json
├─ textures\
│  └─ texture_00.png           # 4096×4096
└─ motions\
   ├─ Background.motion3.json
   ├─ Boring.motion3.json
   ├─ Click_Body.motion3.json
   ├─ Click_Hand.motion3.json
   ├─ Click_Head.motion3.json
   ├─ Click_Leg.motion3.json
   ├─ Click_Leg2.motion3.json
   ├─ Greet.motion3.json
   ├─ Idle.motion3.json
   └─ Level.motion3.json
```

总计 13 个文件。

组装脚本：

```text
D:\codex\_ptn_cdn_research\build_test_model.py
```

组装结果：

```text
moc3：成功
4096 贴图：成功
动作：10 成功 / 0 失败
```

---

## 5. 已注册到本地索引

运行过：

```powershell
python -I tools\build_local_index.py
```

备份：

```text
resources\metadata\local_skins.json.bak
```

当前 `local_skins.json` 共 327 条，含：

```json
{
  "character": "",
  "bundle": "",
  "name": "test",
  "type": "live2d",
  "painting": "test",
  "asset": {
    "dir": "extracted/live2d/test",
    "model": "test.model3.json"
  }
}
```

空 `character` 是有意的：这是验证条目，且项目规则禁止静默猜中文映射。

---

## 6. 软件 UI 显示验收（已通过）

结论：**用户确认渲染正确**（2026-11-11）。

### 6.0 为什么之前「API 有 test 但图鉴不显示」

真正原因不是后端缓存（`ensure_fresh_metadata()` 会按 mtime 自动重载），而是**图鉴前端根本不读 DB**：

```
frontend/src/mock/api.js  loadAll()
  → fetch characters.json / skins.json / local_skins.json
  → 只遍历 skins.json，且要求 sk.character 存在于 characters.json
```

`test` 当时只在 `local_skins.json`，没有 catalog 条目，因此永远进不了图鉴。

修复方式（仅验证用途，不猜中文名）：

- `resources/metadata/characters.json` 增加 `{"name":"test",...}`
- `resources/metadata/skins.json` 增加 `{character:"test",name:"test",type:"live2d",bundle:"",painting:"test",source:"verification"}`
- 原文件备份：`characters.json.pretest.bak` / `skins.json.pretest.bak`

改完无需重启，下一次请求即生效。

### 6.1 验收实测

```
GET /api/library/downloaded   → 302 条，含 test（painting=test / type=live2d）
图鉴页                        → 共 162 位角色 · 329 个装束
搜索 test                     → 命中 1 位，列表末尾 test + L2D 标记
右侧装束面板                  → 按钮 test，✓ 当前已就绪，动画 1 / 10
```

模型结构核对：`test.moc3` 与既有 `resources/extracted/live2d/Enfer_3/Enfer_3.moc3` 完全一致
（1020 drawables / 131 parts / 518 params / 贴图槽仅 1 个索引 0）。

### 6.2 已知差异（非阻塞）

- `test.model3.json` 比 `Enfer_3.model3.json` 少一个 `Idle` 动作组（`All` 中仍有 Idle 文件）。
- `test` 动作更短：Idle 15.767s / 4169 段，Enfer_3 为 31.767s / 7377 段。原因是 `build_test_model.py`
  用的手写 fade `ControlIds` → motion3 转换；本项目日志 §16 已判定该方向不正确，标准做法是
  `tools/assemble_live2d.py` 的 AnimationClip 高保真转换。渲染验收通过，故此项暂不改。

### 6.3 排障提示

标签页处于 `hidden` 时 `requestAnimationFrame` 被暂停，此时用 canvas 读像素会得到全透明，属环境现象，
不代表模型未渲染；需保证预览标签页在前台再取样。

当时开发服务曾在运行：

```text
Vite：127.0.0.1:5173
Backend：127.0.0.1:8766
```

但是 `core.metadata.Metadata` 在后端启动时缓存 `local_skins.json`：

```python
class Metadata:
    def __init__(...):
        self.reload()
```

`test` 是运行中途新增的，因此旧后端进程看不到它。下一位 AI 应先重启开发栈。

### 建议步骤

1. 检查端口：

```bash
netstat -ano | grep -E ":(5173|8766).*LISTEN"
```

2. 安全停止旧开发栈：

```powershell
cd D:\codex\path-to-nowhere-dynamic-wallpaper
python scripts\start_dev.py --stop
```

3. 重新启动源码开发栈：

```powershell
python scripts\start_dev.py
```

如果不需要打开桌面窗口，也可单独重启 `backend_server.py`，但要保留端口 8766。

4. 检查后端 API 是否包含 `test`：

```text
http://127.0.0.1:8766/api/library/downloaded
```

5. 在前端搜索 `test` 并打开 Live2D 预览。

6. 验收以下内容：

```text
模型可见
4096 贴图方向正确
Idle 动作播放
Click_Body / Click_Head / Click_Hand / Click_Leg 可触发
无缺部件
无反向遮罩错误
取景/中心偏移合理
```

### 若 API 有 test 但图鉴不显示

可能原因：`character=""` 且该测试条目不在官方 `skins.json`。处理时：

- 不要猜中文角色名；
- 可添加仅用于验证的测试角色/装束条目，名字就叫 `test`；
- 或直接从调试预览入口打开：

```text
resources/extracted/live2d/test/test.model3.json
```

不要为了测试把它假映射成某个正式角色。

---

## 7. 当前遗留研究问题

### 7.1 希格林德 v77 新内容

希格林德·余晖候令：

```text
Live2d group: 11111103
模型家族: Siglinde_3
6 个 bundle
Version: 77
```

这批 bundle：

- 不在 APK 内置基础切片清单中；
- 扫描真实补丁链 21 段中央目录，0/6 命中；
- 设备本地存在，说明确实通过更新后的分类切片下发；
- 仍缺当前版本对应的增量 chunk/slice 定位清单。

不要把希格林德误报成已支持纯 CDN 按角色下载。

### 7.2 组 ID → 中文角色/装束名

目前验证组 `11060203` 通过解包得到内部名 `Enfer_3`，因此可确认角色是恩菲尔；但项目正式映射仍应遵循：

```text
角色/装束名核实后再写映射
无法核实则进入 review
不按数字顺序猜
```

---

## 8. 关键文件索引

### 项目内

- `docs/CDN_RESEARCH_LOG.md`
- `docs/CDN_RESEARCH_LOG_2.md`
- `docs/CDN_RESEARCH_LOG_3.md`
- `resources/extracted/live2d/test/`
- `resources/metadata/local_skins.json`
- `resources/metadata/local_skins.json.bak`
- `tools/split_ptn_chunk.py`
- `tools/build_local_index.py`

### 研究工作区

- `D:\codex\_ptn_cdn_research\DecryptSecret.java`
- `D:\codex\_ptn_cdn_research\bcprov.jar`
- `D:\codex\_ptn_cdn_research\dl_by_character.py`
- `D:\codex\_ptn_cdn_research\dl_char_out.txt`
- `D:\codex\_ptn_cdn_research\verify_layout3.py`
- `D:\codex\_ptn_cdn_research\build_test_model.py`
- `D:\codex\_ptn_cdn_research\dl_char_11060203\`
- `D:\codex\_ptn_cdn_research\verify_work\`
- `D:\codex\_ptn_cdn_research\ghidra2\`
- `D:\codex\_ptn_cdn_research\jdk25\`
- `D:\codex\_ptn_cdn_research\decomp_out_annotated.c`

---

## 9. 安全和操作约束

1. 不要打包发布，先只做源码 UI 验收。
2. 不要删除 `resources/extracted/live2d/test`。
3. 不要重跑 26GB 游戏全量下载。
4. 不要运行 Live2DTools 的默认 C 盘提取流程。
5. 若必须运行 Live2DTools，显式设置 D 盘的：

```text
TMP=D:\codex\_ptn_cdn_research\verify_work\tmp
TEMP=D:\codex\_ptn_cdn_research\verify_work\tmp
```

6. 不要把研究工作区里 20GB 的 pcap 或大模型复制到 C 盘。
7. 不要猜角色/装束中文名；无法核实的进 review。
8. CDN 交互保持公开 GET/Range，不增加鉴权绕过。

---

## 10. 下一位 AI 建议首句

> 我已阅读 `docs/HANDOFF_CDN_LIVE2D_TEST.md` 与 `docs/CDN_RESEARCH_LOG_4.md`。
> CDN 分类通道已全链路接入软件（下载按钮/后端/装配/动作名规范化/动画均完成）。
> 遗留：①res v12+ 刷新后重拉设备明文清单，解锁剩余 3 组与新皮肤；②Level 动作时长分歧
> 与 CDN 动作 30Hz 平滑度待用户确认；③本地 8.8GB 资产备份待还原
> （D:/codex/_backup_ptn_extracted_live2d，跳过 Siglinde_3/Enfer_5）。不要重复逆向 CDN。

---

## 11. 2026-11-12 第五轮：新容器格式与四星皮两阶段

### 11.1 一句话结论

游戏 11-11 更新后容器换成「50B 明文头 + 明文 UnityFS v8 + 64 字节对齐、混合裸块」；
**清单与文件名 grep 都不再可信**，资源定位必须用内容扫描（UnityPy 读 AssetBundle/moc 名）。
四星皮是 `_<seq>` 与 `_<seq>01` 两个独立包。

### 11.2 已完成的产物

| 模型 | 来源 | 规格 |
|---|---|---|
| `Shatila_2`（莎缇拉·升阶） | `fsclassify` 组 11061602 的 5 个 bundle | moc 3.69MB / 99 部件 / 221 参数 / 9 动作 / 4096 贴图 |
| `Shalom_6`（瑟琳·微明止息·阶段一） | 共享目录 `fs` 的 `1df007d7…`(moc)、`83770c8c…`(2×4096)、`9028f903…`(动画) | moc 7.62MB / 124 部件 / 463 参数 / 12 动作 |
| `Shalom_601`（瑟琳·微明止息·阶段二） | 共享目录 `fs/assets` 的 `9d5e826e…`(moc)、`977dff7e…`(4096)、`fb49ec65…`(动画) | moc 35.86MB / 106 部件 / 317 参数 / 8 动作 |

三者均已在前端实际渲染验收；图鉴条目见 `skins.json`：`shatila_2`、`shalom_6`、`shalom_601`
（瑟琳两条显示为「微明止息 · 阶段一 / 阶段二」）。

### 11.3 关键修复

1. `tools/assemble_classify.py`：`body = raw[i_v8:] if i_v8 > 0 else None` → `i_v8 >= 0`。
   裸 UnityFS（偏移 0）此前会被跳过，直接导致「未提取到 texture_* 贴图」。
2. 新增 `tools/export_from_emulator.py::iter_chunk_bodies`：兼容旧 XOR 与新明文 v8、
   带前缀/裸块混排、64 字节对齐；`probe_bundle` 用于内容级识别。
3. 同源装配：moc / 贴图 / 动画必须来自同一构建。`shalom_601` 两套贴图实测 78.9% 像素不同，
   混用会碎片化。

### 11.4 仍未解决

- `coquelicot_501`（可可莉克·绯夜初醒·阶段二）：moc 4.35MB + 动画 143KB 已在
  `res_be8afb…` / `res_0627cd…` 中找到，但**贴图包不存在**（15GB 共享目录 + 75 chunk 全扫无命中）。
  用 501 moc 配 5 的贴图渲染会碎片化，确认两阶段贴图不通用。
- 需要客户端完整加载该阶段后再提取；在此之前图鉴只保留阶段一。

### 11.5 定位资源的正确姿势（新）

```text
1) 直接读 D:/文档/MuMu共享文件夹/files（fs / fsclassify / fschunk），不必 adb
2) 遍历 *.bundle 与 chunk 块，用 UnityPy 读 AssetBundle.m_Name / MonoBehaviour.m_Name
3) 找到后按家族（moc/tex/animation/material/effect）复制到同一 staging 目录
4) assemble_from_group(staging, out_root, painting) → 前端渲染验收
```

不要再用：`classify_group_map` 缺组、`grep 文件名` 无命中、旧 `res_chunk_manifest`
来判断资源是否存在。

### 11.6 第三方工具（无期迷途Live2D提取工具.exe）

- 位置：`Live2DTools/`（PyInstaller，解包核心 `_internal/unpacker/PtnExtract.dll` + `AssetStudio.dll`，10-17 构建）。
- 失效原因：不认新版 UnityFS v8 / 新容器；表现为能列出模型名，但 `moc:0 / fade:0`，最终「处理未完成」。
- 结论：闭源 DLL 无法可靠打补丁；继续用 `tools/export_from_emulator.py` 或按 11.5 的流程替代。

### 11.7 classify 直链下载（本轮新增）

- 规则：`.../major/2.5/classify/{entry.Version}/classify/{entry.Name}`（Name 去前导 `/`）。
- ⚠️ 版本号取**每条清单自己的 Version**，不是全局 `slice_version`；固定用 0 会大量假 404。
- 清单：`resources/metadata/classify_download_manifest.json`（设备明文 3580 条）。
- 实测：组 11061602（莎缇拉）5/5 直链下载，MD5 全对；装配结果与设备提取版 MD5 一致。
- 清单缺的条目回退旧 slice+Range；仍缺的（如 `coquelicot_501` 贴图）直链也无解。
