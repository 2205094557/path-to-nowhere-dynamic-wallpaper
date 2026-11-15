# 无期迷途资源 CDN 探查与实现全过程

> 日期：2026-11-09 ～ 2026-11-10  
> 项目：`D:\codex\path-to-nowhere-dynamic-wallpaper`  
> 研究产物：`D:\codex\_ptn_cdn_research`  
> 本文记录为实现“按角色/装束从官方资源通道下载并提取壁纸素材”所尝试的全部主要步骤、结果、失败原因和最终结论。登录 token、设备 ID、账号参数等均已脱敏。

---

## 1. 初始目标

参考既有项目实现无期迷途版本：

```text
角色/皮肤图鉴
→ 找到官方资源下载通道
→ 按角色定位资源
→ 下载 AssetBundle
→ 提取静态立绘或 Live2D
→ 预览并导出 Wallpaper Engine
```

需要解决的技术问题：

1. 官方 CDN 域名和版本接口是什么。
2. 游戏如何获得资源清单。
3. 资源的 URL 规则是什么。
4. 下载文件是否加密、拼接或压缩。
5. 如何将内部资源名映射到中文角色和皮肤名。
6. 是否可以在没有模拟器的电脑上按角色直接下载。

---

## 2. 分析既有项目的 CDN 模式

先只读分析了碧蓝航线与棕色尘埃 2 项目。

### 2.1 碧蓝航线

链路：

```text
TCP + protobuf 握手
→ 取得 azhash / paintinghash / l2dhash 版本串
→ GET /android/hash/{版本串}
→ 明文 CSV: path,size,md5
→ GET /android/resource/{md5}
```

### 2.2 棕色尘埃 2

链路：

```text
PUT MaintenanceInfo
→ 得到 bundle_version
→ GET catalog_alpha.json
→ 解 Addressables catalog
→ 定位 bundle
→ 直接 GET bundle
```

共同结论：两个项目都需要先知道游戏专属“阶段 0”信息，即域名、版本接口和清单格式；这些信息通常来自开源逆向参考或抓包实测。

---

## 3. BWiki 图鉴 PoC

在正式研究 CDN 前，先验证角色与装束图鉴来源。

建立了独立项目：

```text
D:\codex\wqmt-bwiki-poc
```

结果：

- 角色：160 个；后续加“局长”变成 161 个。
- 装束：241 条 BWiki 官方条目。
- 角色图片覆盖 100%。
- 装束图片覆盖约 98.3%。
- 3 张样本下载验证通过。
- BWiki 存在 HTTP 567/429 限流。

此阶段只解决图鉴中文数据，不解决游戏资源路径。

---

## 4. 连接 MuMu 模拟器

发现本机安装 MuMu，ADB 在：

```text
D:\Program Files\Netease\MuMu\nx_main\adb.exe
```

连接端口：

```text
127.0.0.1:16384
```

确认包名：

```text
com.zy.wqmt.cn
```

游戏外部数据目录：

```text
/sdcard/Android/data/com.zy.wqmt.cn/files/
├─ fs
├─ fsaudio
├─ fschunk
├─ fsclassify
├─ fstier1
├─ il2cpp
└─ ...
```

从 `fs/` 拉取：

```text
app_version.json
res_version.json
res_time_stamp.json
res_manifest
```

实测版本：

```json
app_version = 2.4.67
res_version = 2（后续完整更新到 101）
revision = 1092490
```

---

## 5. 从应用配置发现官方热更入口

模拟器可 `adb root`，因此读取应用私有 SharedPreferences。

在：

```text
/data/data/com.zy.wqmt.cn/shared_prefs/com.zy.wqmt.cn_prefer.xml
```

发现：

```text
https://xonecn-hotupdatecdn.shziyi.com/release-cn/android/tags/major/2.3/latest/res_releases.json
```

以及网络诊断目标：

```text
xonecn-hotupdatecdn.shziyi.com
xonecn-sh-public-resource.shziyi.com:8090
xonecn-sh-public-auth.shziyi.com
xonecn-sh-s1/s2/s3-gateway.shziyi.com
```

根据当前客户端版本测试 `major/2.4`。

---

## 6. 验证公开版本接口

实测以下 URL 无鉴权可读：

```text
https://xonecn-hotupdatecdn.shziyi.com/
  release-cn/android/tags/major/2.4/latest/res_releases.json

https://xonecn-hotupdatecdn.shziyi.com/
  release-cn/android/tags/major/2.4/latest/res_version.json
```

结果：

- 当前资源版本：101。
- Releases 列表约 101 个版本。
- `Merges` 中存在合并版本信息。

`latest/` 目录只直接提供版本索引，并不直接提供完整 manifest/hash/classify 文件。

---

## 7. 抓取游戏正常更新流量

### 7.1 tcpdump

模拟器内置：

```text
/system/bin/tcpdump
```

首次抓包发现所有游戏下载流量走 HTTPS，明文无法直接看到路径，但 TLS SNI 确认访问：

```text
xonecn-hotupdatecdn.shziyi.com
```

### 7.2 MITM 抓包

检查 APK 的 `network_security_config.xml`，确认应用信任 system + user CA，因此临时安装 mitmproxy CA 并设置模拟器代理。

抓到配置接口：

```text
POST https://api.shziyi.com:12101/v1/gameconfig/patchlist
```

响应中得到：

```json
{
  "hotfix_version": "2.4.101",
  "cdn_url": "https://xonecn-hotupdatecdn.shziyi.com,https://xonecn-hotupdatecdn-backup.shziyi.com"
}
```

登录 token、设备参数等未写入文档。

抓到版本检查链：

```text
GET .../latest/res_releases.json
GET .../patches/2_5.patch.txt
GET .../patches/5_10.patch.txt
...
GET .../patches/96_101.patch.txt
```

---

## 8. 增量补丁机制

补丁元信息格式：

```json
{
  "FileCount": 222,
  "Version": 101,
  "Filename": "96_101.patch",
  "FileMd5": "...",
  "FileSize": 198894845
}
```

补丁本体 URL：

```text
{cdn}/release-cn/android/tags/major/2.4/patches/{from}_{to}.patch
```

实测补丁是标准 ZIP，内含：

```text
assets/
blocks/
assemblies/
wwise/
res_manifest
res_build_info.json
res_media.json
res_classify*.json
...
```

部分 `res_*.json` 明文，部分 AES-ECB 加密。

20 段补丁总量约：

- 文件记录合计约 20,439。
- 下载量约 9.9GB。

---

## 9. 官方 chunk 下载协议

游戏资源下载确认页提供：

```text
完整资源：约 26.45GB
基础资源：约 17.83GB
```

确认下载后，MITM 抓到：

```text
GET https://xonecn-hotupdatecdn.shziyi.com/
  release-cn/android/tags/major/2.4/chunk/res_{32hex}.bin
```

实测：

- 无额外登录鉴权。
- 支持 Range，返回 HTTP 206。
- 单个 chunk 多为约 100MB。
- 游戏本地 `fschunk/` 文件与 CDN 同名同内容。

示例：

```text
res_1d6823a6f2c513490453669017858916.bin
res_82911cf38a8cb7a9343d4793b353890f.bin
...
```

---

## 10. chunk 容器格式逆向

最初按全文件 XOR 0x42 解码时看到多个 UnityFS 魔数，误以为是任意间隔拼接。

进一步全文件扫描并对照公开社区脚本后，最终格式确认：

```text
chunk = block1 + block2 + ...

每个 block：
[50B 前缀]
[经可变单字节 XOR 的 UnityFS 数据]

key = raw[offset + 50] XOR 0x55
校验：raw[offset + 51] XOR 0x6E == key
解密后头：UnityFS\0
UnityFS size 位于头内固定字段
下一块 offset = offset + 50 + unityfs_size
```

实现：

```text
tools/split_ptn_chunk.py
```

验证：

- 7 个真实 chunk 全通过。
- 共拆出 3,407 个 UnityFS。
- 7/7 文件字节完全覆盖。
- 抽样 83/83 UnityFS 可被 UnityPy 加载。

后续增加了磁盘流式 `iter_blocks`，避免整个 100MB chunk 一次载入内存。

---

## 11. 静态立绘扫描与角色匹配

拆分后的 Texture2D 使用规律：

```text
lihui_{角色内部名}_{资源键}
```

例如：

```text
lihui_zoya_default
lihui_zoya_zhanzi
lihui_zoya_prisonphoto
```

使用 PtNData `Hero_Profile.json`：

```text
中文姓名
Internal Name
MBCC 编号
```

经 jsDelivr 获取，生成：

```text
name_aliases.json
```

实现：

```text
tools/build_name_aliases.py
tools/build_ptn_metadata.py
```

匹配结果：

- 早期 883/883 静态资源归属成功。
- 完整基础资源扫描后得到 2,252 个静态资源定位记录。

静态类别确认：

```text
zhanzi       → 立绘
default      → 升阶
prisonphoto  → 证件照
handbook     → 档案
trail        → 审查
```

尾部 `2/3` 多为缩略图、差分或 UI 变体，不作为主分类。

---

## 12. chunk 清单与 AES 清单尝试

APK 与本地存在：

```text
res_chunk_manifest.json
res_classify.json
res_hero_name.json
...
```

确认：

- AES-ECB 固定密钥特征。
- 密文有大量重复 16B 块。
- 不同版本共享很长的密文前缀。

尝试过：

1. 常见字符串/AES 密钥字典。
2. 包名、游戏名、域名及其 MD5/SHA 派生。
3. global-metadata 中 Base64/hex 字符串候选。
4. metadata 全部 16B 和 32B 滑动窗口。
5. libil2cpp.so 全部 16B 和 32B 滑动窗口。

结果均未命中。

最终停止破解，改用游戏已下载目录引导：

```text
fschunk/ 目录列表 = chunk 名单
```

用户完成基础资源下载后，得到 63 个 chunk 名称，写入：

```text
resources/metadata/chunk_list.txt
```

---

## 13. 全量基础资源流式构建

实现：

```text
tools/build_library.py
```

流程：

```text
下载一个 chunk
→ 流式拆块
→ 扫 Texture2D / MOC3
→ 提取目标数据
→ 删除大 chunk
→ 继续下一个
```

63/63 chunk 完成后得到：

```text
静态资源：2252
MOC3 模型：21
```

定位信息写入：

```text
resources/metadata/library_state.json
resources/metadata/live2d_inventory.json
```

---

## 14. 按角色下载实现

来源插件：

```text
plugins/sources/ptn_cdn.py
```

后端流程：

```text
skins.json 中的 painting/internal_model
→ library_state 查所属 chunk
→ Range 断点下载 chunk
→ 流式拆分
→ 只提取目标 Texture2D 或 Live2D 家族
→ 删除完整 chunk
→ 更新 local_skins.json / library.db
```

实现位置：

```text
backend_server.py: download_skin
```

支持：

- SSE 进度。
- 取消。
- `.part` 断点续传。
- 已下载跳过。
- 完成/失败后清理大 chunk。

真实静态 E2E：

```text
lihui_luminita_default2
→ 官方 CDN 下载 chunk
→ 提取 600×600 PNG
→ 与预先备份文件 SHA-256、像素完全一致
→ chunk 自动删除
```

---

## 15. 增量补丁 ZIP Range 下载尝试

为了寻找基础包以外的新装束，建立了远程 ZIP Range 读取器：

- 只读 ZIP EOCD 和中央目录。
- 找到目标 entry 的本地头。
- HTTP Range 只下载该 entry 的压缩字节。
- Deflate 解压。
- 校验 uncompressed size 和 CRC32。

定位到：

```text
希格林德·余晖候令
补丁：76_81.patch
主立绘：assets/98/98d2673b36b470d5.bundle
内部名：lihui_siglinde_yuhuihouling
尺寸：2040×1632
```

真实验证：

- 仅下载约 1.18MB 压缩数据。
- 约 1.53 秒完成。
- 解出 2040×1632 RGBA 图。

但用户后来明确只要动态立绘，因此撤销把此静态条目作为目标的路线。

---

## 16. Live2D 基础模型研究

基础 chunk 中最初发现 21 个 MOC3，例如：

```text
Zoya_501
Nuokesi_501
Hameier_501
...
```

一个 Live2D 家族通常包括：

```text
<model>.bundle              MOC3
<model>.<resolution>.bundle Texture2D
<model>_motion.bundle       Fade Motion
<model>_animation.bundle    AnimationClip
build_prefabs_live2d...     运行时 Prefab
build_animations2d...       时间轴
```

装配器：

```text
tools/assemble_live2d.py
```

已实现：

- 精确读取 MonoBehaviour `_bytes` 中的 MOC3。
- 提取最高分辨率贴图。
- sssekai AnimationClip → motion3。
- 通过 MOC Parts/Parameters 重建 CRC 映射。
- Idle + Background 双层动作合并。
- textureFlipY。
- 实际 drawable 包围盒取景。
- `CubismRenderControllerV2.materialIds` 转 Web Cubism flags。

早期手写 `.fade ControlIds` 映射是错误方向，已改用 AnimationClip 高保真转换。

---

## 17. 完整 Live2D 分类资源

用户在游戏内下载完整 Live2D 资源后，发现：

```text
fschunk 仍是 63 个
fstier1：约 1.1GB，主要语音/一级资源
fsclassify/assets：约 5.7GB，3830 个独立 bundle
```

扫描 `fsclassify/assets` 后得到：

```text
约 303 条 Live2D 家族记录
约 298 个带 MOC3 的模型
```

希格林德完整模型：

```text
Siglinde_3
├─ 2ab754daab505104.bundle  MOC3
├─ 82a1d2281a1115fc.bundle  4096贴图
├─ ca21cb8d1447877e.bundle  Motion/Fade
├─ fc7dfa7546f65345.bundle  AnimationClip
├─ 8e8eb178f03d9c19.bundle  运行时Prefab/材质
└─ 57144976928ae0d3.bundle  Timeline
```

已装配：

- MOC3 约 2.7MB。
- 4096×4096 贴图。
- Idle / Background / Click Body 动作。
- 392 个 drawable 材质 ID。
- 映射：`Siglinde_3 → 希格林德·余晖候令`。

---

## 18. 分类资源 CDN URL 尝试

为了让其他电脑不依赖模拟器下载 `fsclassify`：

1. 临时备份并移走一个 14KB Motion bundle。
2. 安装临时 CA、设置系统代理。
3. 重启游戏，等待“还有154M资源需要下载”提示。
4. 尝试通过 mitmproxy 捕获分类下载 URL。

踩坑：

- MuMu 截图是横屏 2560×1440。
- `wm size` 报自然方向 1440×2560。
- ADB `input tap` 使用自然方向坐标，必须旋转换算。
- 前两次点错坐标却误判成功，这是执行错误。
- 正确点击后游戏进入加载，但分类下载线程没有通过系统 HTTP 代理；MITM 未捕获到资源请求。
- tcpdump 启动时机过晚，也没有得到可用 URL。

每次结束后均恢复：

- 被移走的 bundle。
- 文件权限。
- `http_proxy=:0`。
- 删除临时 CA。
- 删除模拟器内抓包文件。

当前结论：

- 分类资源官方 URL 规则尚未获得。
- 不应继续反复操作游戏抓包。
- 可先基于已下载本地文件完成软件目录与预览。

---

## 19. 他人提取的 Live2DOutput

用户放入：

```text
Live2DOutput.zip / Live2DOutput/prefabs/live2d
```

统计：

```text
179 个角色目录
185 个 MOC3
189 个 model3.json
189 张纹理
1289 个 motion3.json
2 个 physics3.json
```

目录已是标准 Cubism 成品结构，可直接被当前预览和 WE 导出使用。

价值：

- 不再需要自己恢复 MOC3/贴图/动作。
- 可作为旧角色的成熟成品库。
- 可用于对比当前提取器的 model3/motion3 格式。

限制：

- 属于较早版本，不含希格林德等新角色。
- 模型数字后缀仍需映射中文皮肤名。

---

## 20. 角色目录构建

最终按角色显示：

```text
基础资源
  立绘 / 升阶 / 证件照

补充资料
  档案 / 审查

动态皮肤
  已核实中文皮肤名
```

实现：

```text
tools/build_character_catalog.py
resources/metadata/live2d_skin_map.json
frontend/src/mock/api.js
frontend/src/features/gallery/GalleryView.vue
```

规则：

- 只显示主资源。
- `default2/default3/zhanzi2` 等不单独展示。
- 未核实数字模型不猜中文名，进入 review。
- 当前选择使用稳定 resource key，不再依赖数组下标。

源码 GUI 已验证：

- 卓娅：立绘/升阶/证件照/档案/审查/动态皮肤。
- 希格林德：立绘/升阶/证件照/档案/审查/余晖候令。

---

## 21. 最终已确认的下载能力

### 可完全独立实现

1. 版本检查。
2. 基础 chunk 下载。
3. Range 断点续传。
4. chunk 拆分。
5. 静态立绘按角色定位和提取。
6. 基础 chunk 中的 Live2D 按模型定位和装配。
7. 补丁 ZIP 中的单 entry Range 下载。

### 仍依赖本地已下载资源

1. `fsclassify` 完整可选 Live2D 资源。
2. 新角色/新皮肤的模型家族扫描。
3. 分类资源散装 bundle 的官方 URL 规则。

---

## 22. 主要教训

1. 本地单文件检查不要调用子智能体。
2. 数据源或路线被限流/阻塞时及时换路，不要重复退避。
3. 功能未稳定前只测源码，用户明确确认后才打包。
4. 用户要动态时，不要扩展静态路线。
5. 点击模拟器按钮后必须截图确认，不能凭状态猜成功。
6. 抓包前先验证代理监听、坐标和触发条件。
7. 不破解没有必要破解的清单；已有本地目录可直接作为引导。
8. 角色/皮肤内部编号不能按数字顺序猜中文名。
9. Live2D 不只需要 MOC3/贴图；还可能需要 Animation、Prefab、材质和时间轴。
10. 发布前必须确认图鉴条目实际可下载，不能只确认后端健康。

---

## 23. 当前关键文件

```text
plugins/sources/ptn_cdn.py            CDN 与定位
tools/split_ptn_chunk.py              chunk 流式拆分
tools/build_library.py                全量基础库扫描
tools/assemble_live2d.py              Live2D 家族装配
tools/build_name_aliases.py           中文名/内部名
tools/build_character_catalog.py      角色目录分类
tools/build_release_catalog.py        发布资源目录
resources/metadata/chunk_list.txt      63个基础chunk
resources/metadata/library_state.json  资源→chunk定位
resources/metadata/name_aliases.json   角色内部名映射
resources/metadata/live2d_skin_map.json 已核实动态皮肤映射
resources/metadata/ptn_render_profiles.json 材质映射
```

---

## 24. 后续建议

按用户当前要求，优先：

1. 只基于已下载游戏文件与 `Live2DOutput` 完成角色目录。
2. 批量导入标准成品模型。
3. 只对可验证模型建立中文皮肤映射。
4. 暂停分类 CDN URL 抓包。
5. 继续只用源码测试，不打包，直到用户确认。
