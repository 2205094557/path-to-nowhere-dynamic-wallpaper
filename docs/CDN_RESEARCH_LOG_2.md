# 无期迷途分类资源（fsclassify）下载通道研究 · 第二轮

> 日期：2026-11-11
> 承接：`CDN_RESEARCH_LOG.md` 第 18 节「分类资源 CDN URL 尝试」
> 本轮目标：查明分类资源（Live2D 等可选内容）的官方获取机制，评估能否复刻 nobbyfix 式独立下载

---

## 0. 结论速览

| 问题 | 结论 | 证据等级 |
|---|---|---|
| 分类元数据通道 | 自定义 TCP RPC（`xonecn-sh-public-resource.shziyi.com` 同集群 `203.107.63.152:8094`） | **实测**（tcpdump 流重组） |
| 分类内容通道 | 与基础资源**同一条 CDN chunk 链路**（`ExtraArtChunks`） | **实测+静态**（流量 + il2cpp 类结构） |
| 分类内容落地形态 | `fsclassify/assets/{xx}/{16hex}.bundle` 散装文件（3830 个） | **实测**（全量重下后清点） |
| 系统代理 / MITM | **游戏自定义网络层不兼容全局代理**，设置后启动更新直接失败 | **实测**（两次复现） |
| 清单文件加密 | AES-ECB（16B 块，30.5% 重复块），**密钥非静态常量** | **实测**（统计+穷举） |
| 客户端是否存在 AES 解密调用 | **il2cpp 中无任何 .NET Aes/Rijndael 调用点** | **静态**（全量 BL 扫描） |
| 独立复刻 RPC | 未完成，缺清单密钥/RPC schema | 未验证 |

**一句话**：分类内容的"URL 规则"问题已经转化——它不走独立 URL，而是复用已验证的 chunk 通道；真正的卡点变成「如何获得分类 chunk 名（md5）」和「RPC 的加密语义」。

---

## 1. 本轮验证过程（按时间）

### 1.1 全量删除重下实验

删除 `fs/fschunk/fsclassify/fstier1/fsaudio`（合计 22.6GB），重启游戏：

- 游戏弹出「下载管理」对话框：完整资源 26.45GB（默认勾选）/ 基础资源 17.83GB。
- 确认后全量下载，实测速率峰值远高于早期估计，**约 40 分钟完成 26.45GB**。
- 全程 tcpdump 抓包（`-i any -s 0`）。

### 1.2 通道判定（关键）

抓包结论：

```
下载本体：IPv6 HTTPS → 2408:8722:8800:3:3::1c
          该地址 = xonecn-hotupdatecdn.shziyi.com 的 AAAA 记录（已实测对照）
          17 条并行 TCP 连接
元数据：  203.107.63.152:8094 自定义 RPC（期间无内容流量）
```

- IPv6 地址与 CDN 域名的对应关系通过宿主机 DNS 查询确认：
  `getaddrinfo("xonecn-hotupdatecdn.shziyi.com") → 219.154.251.69/.74 与 2408:8722:8800:3:3::1c/::1d`
- 因此**分类资源与基础资源共用同一 CDN**，仅有版本/清单差异，不存在独立域名。

### 1.3 8094 RPC 协议帧（实测样本）

```
C2S: [4B len][2B cmd][8B flags][protobuf]
     cmd 0x2710 = 10000  握手（protobuf 含 40 字符 hex token）
     cmd 0x2711 = 10001  业务请求（48B 加密负载）
     cmd 0x2712 = 10002  其他
S2C: 回显请求头 + 加密响应体（含 256 字符 hex 字段，疑似签名/票据）
```

- S2C 单次响应约 152KB，几乎全为密文。
- 该 RPC 不走 HTTP，也不走系统代理，这是第 18 节"分类下载线程抓不到"的真正原因。

### 1.4 il2cpp 静态逆向（Il2CppDumper + 自研反汇编）

工具链：
- Il2CppDumper 6.7.46（用户代理下载）→ `dump.cs`（43MB）/ `script.json`（125MB）/ `stringliteral.json`
- 自研 `disasm2.py`：capstone + ELF 重定位表（`R_AARCH64_RELATIVE`）解析 → **完整还原 ADRP+LDR 槽位到字符串/方法名**

核心类（`Framework.Installer`）：

```csharp
public class AssetsChunkInfo {
    List<PatchInfo> BaseArtChunks;      // 基础美术 chunk
    List<PatchInfo> BaseAudioChunks;
    RemoteFileManifest BaseArtManifest;
    ...
    List<PatchInfo> ExtraArtChunks;     // ← 分类（Live2D/插画）美术 chunk
    List<PatchInfo> ExtraAudioChunks;
    RemoteFileManifest ExtraArtManifest;
}
public enum AssetType { Mainline=1, Activity=2, Trial=3, Live2d=4, Illustration=5 }
public sealed class ClassifiedAssetManifest {
    Dictionary<AssetType, Dictionary<int, RemoteFileManifest>> _data;  // 组 id = 分类组
}
public sealed class RemoteFileInfo { ... public string cdnRelativePath; }  // 每个文件自带 CDN 相对路径
```

`InstallerModule` 关键方法（RVA 已记录）：

| 方法 | RVA |
|---|---|
| `EnsureAssetInstalledBeforeLaunch` | 0x365CD2C |
| `CheckBaseAssetDownloadInfos` | 0x365D04C |
| `CheckClassifyDownloadInfos` | 0x365D888 |
| `CheckChunkDownloadInfos` | 0x365E330 |
| `DownloadFilesAsync(fileList, resVersion, ...)` | 0x365EC10 |

`AddDownloadTaskFromCdn(RemoteFileInfo info, string cdnRelativePath, string localFolder, ...)` 证实：**最终 URL = CDN 基址 + cdnRelativePath**，相对路径来自清单数据。

### 1.5 清单加密分析

| 文件 | 大小 | 状态 |
|---|---|---|
| `res_manifest` | 2.87MB | **明文**（FlatBuffers，24537 条 `/assets|blocks|assemblies/{xx}/{16hex}.bundle`） |
| `res_media.json` | 19.5KB | **明文**（`{Name, Hash=内容md5, Size}`，Hash 已实测校验） |
| `res_classify_download_setting.json` | 485B | **明文**（`ClassifyLive2dGroupIds=[11021103,...]`，疑似 角色+皮肤） |
| `res_classify.json` | 482,880B | AES-ECB 加密 |
| `res_base_classify.json` | 769,760B | AES-ECB 加密 |
| `res_base_classify_chunk.json` | 582,992B | AES-ECB 加密 |
| `res_chunk_manifest.json` | 1,551,184B | AES-ECB 加密 |

加密判定：16B 块重复率 30.5%，8B/16B 对齐，非循环 XOR（XOR 差分分布平坦），真块密码。

**密钥穷举记录（负结果，可复用）**：
1. 静态候选：包名/游戏名/域名及 MD5/SHA 派生、metadata 全 16/32B 滑窗、libil2cpp 全滑窗 —— 均无效（上轮）
2. 本轮：metadata 全部 127,895 个字符串的 md5/sha1/sha256 派生（905,195 次）—— 无效
3. 本轮：hex/base64 字符串解码派生 —— 无效
4. 本轮：**400MB 堆内存全部 16B 窗口（AES-128）与 32B 窗口（AES-256）**—— 无效
5. il2cpp 全量 BL 扫描：**无任何 .NET `Aes`/`RijndaelManaged.CreateDecryptor` 调用点**

结论：密钥要么服务端下发（配合 8094 RPC），要么由 native 层（libzipw / 其他）以未知方式派生。**客户端 C# 层不解密这些文件**。

### 1.6 环境与工具副作用（经验）

- MuMu 12 为 x86_64 + ARM 翻译层；**Frida native Interceptor 会导致游戏崩溃**（实测一次）。frida-server 17.26.2 x86_64 已可部署（`/data/local/tmp/fsrv`），仅适合纯内存读取。
- tcpdump `-s 1600` 会截断 TLS1.3 ClientHello（含后量子 key share 时 >1600B），**抓 SNI 必须 `-s 0`**。
- MuMu 上 `input tap` 使用**自然方向 1440×2560** 坐标；横屏截图坐标 (lx,ly) → 自然坐标 (ly, 2559-lx)。
- 26GB 下载配 `-s 0` 抓包会产出 20GB+ pcap，注意磁盘。
- `toybox grep` 不支持 `\|` 交替；脚本里要用 `grep -E`。
- Git Bash 下 adb push/pull 的 Windows 路径需 `MSYS_NO_PATHCONV=1` 或正斜杠。
- MuMu 虚拟机在高 IO 下会整体 hang（adbd offline），`MuMuManager.exe control -v 0 restart` 可恢复。

---

## 2. 当前能力矩阵

| 能力 | 状态 |
|---|---|
| 基础资源 chunk 下载（63 个） | ✅ 生产可用 |
| 静态立绘按角色定位/提取 | ✅ 生产可用 |
| 补丁 ZIP 单 entry 远程 Range | ✅ 生产可用 |
| 新版本内容（含新皮肤 Live2D）获取 | ⚠️ 补丁通道可拿到 bundle，但**分类 bundle 从不进补丁**（交集实测=0） |
| 分类 bundle 独立下载 | ❌ 缺 chunk 名单（md5）与 RPC 协议 |
| 游戏代下 + 本地提取 | ✅ 可用（本次 22.6GB 重下验证） |
| Live2D 装配/导出 | ✅ 已有 |

---

## 3. 剩余未知与下一步选项

### 选项 A（推荐，成本最低）：抓「暂存期 chunk 文件」
分类 chunk 下载后必须落地再解包。静态字符串中已见暂存目录：
`__patch_staging__` / `__patches__` / `__tier1__` / `__tier2__` / `Download`。

做法：删 1 个分类 bundle → 在游戏内触发该内容（或设置页「资源下载」）→ **每 1 秒轮询所有暂存目录 + fschunk**，抓到 `res_{32hex}.bin` 文件名 → 用已验证的 chunk URL Range 下载核对。

### 选项 B（成本中）：RPC 协议复刻
需补齐：0x2710 握手语义（40hex token 与 256hex 返回的关系）、0x2711 负载加密方式。
建议用 Ghidra headless 反编译 `EnsureAssetInstalledBeforeLaunch`(0x365CD2C) 与 `CheckClassifyDownloadInfos`(0x365D888)，重点看与 8094 socket 交互的部分。

### 选项 C（成本低，已可执行）：混合路线
`fsclassify` 内容以「游戏代下」维护本地镜像；新版本差异走补丁通道。工具侧不需要 URL 规则。

---

## 4. 本轮新增工具（均在 `D:\codex\_ptn_cdn_research`）

| 文件 | 作用 |
|---|---|
| `disasm2.py` | il2cpp 定向反汇编（重定位→字符串/方法名还原） |
| `find_xref.py` | ADRP+ADD/LDR 代码 xref 扫描 |
| `find_calls.py` | 全量 BL 扫描定位方法调用点 |
| `remote_zip_ls.py` | 远程 ZIP 中央目录读取（补丁索引） |
| `pcap_analyze.py` | SLL2 pcap 解析（端口/SNI/HTTP） |
| `heap_key_sweep2.py` | 堆窗口 AES 密钥扫描 |
| `apk_cd.py` / `apk_extract.py` | APK 中央目录/条目远程提取 |
| `memscan3.sh` | 设备内存扫描（管道缓冲写） |

---

## 5. 关键文件位置

```
D:\codex\_ptn_cdn_research\
├─ libil2cpp.so / pulled/global-metadata.dat    原始逆向素材
├─ icd_out/  dump.cs · script.json · stringliteral.json
├─ apk_res/  APK 内加密清单原样提取
├─ memscan/  游戏堆内存转储（02:17 时点）
└─ cls_classify.pcap（20GB，含 26GB 下载期流量）
```
