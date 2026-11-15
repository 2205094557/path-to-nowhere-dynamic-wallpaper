# 分类资源（fsclassify / Live2D）按角色官方 CDN 下载 —— 已跑通

> 日期：2026-11-11（第三轮，承接 `CDN_RESEARCH_LOG_2.md`）
> 状态：**端到端验证通过**，不依赖模拟器/游戏客户端，纯公开 GET

---

## 0. 结论

**是，已经做到按角色从官方下载。** 实测：从 CDN 用 HTTP Range 只取回某个角色 Live2D 组的 9 个 bundle（合计 23.13 MB），**9/9 的 MD5 与官方清单完全一致**，且解码后均为标准 UnityFS（Unity 2019.4.40f1）。

关键突破是搞清楚了之前一直解不开的三件事：

| 之前的误解 | 实际情况 |
|---|---|
| 清单是 AES-ECB 加密，密钥找不到 | **Twofish**（BouncyCastle `PaddedBufferedBlockCipher`），密钥是客户端常量 `D(G+KbPeShVmYq3t` |
| 分类资源走 `chunk/res_*.bin` | 走 **`classify/{Version}/classify/res_{md5}.slice`**（双 classify） |
| 需要下整个 100MB 切片 | slice 是 `[512B 头][payload][512 对齐]` 拼接，可 **Range 精确取单个 bundle** |

---

## 1. 完整链路

```
APK / 游戏目录里的加密清单
  └─ Twofish-ECB + PKCS7, key=UTF8("D(G+KbPeShVmYq3t")
       └─ 解密得到：
            res_base_classify.json        按类别→groupId→bundle 列表（Name/Hash=MD5/Size/Version）
            res_base_classify_chunk.json  BaseArt/ExtraArt/Audio 的 slice 清单 + 有序总表
            res_classify.json             当前版本全量 3830 个 bundle
            res_chunk_manifest.json       基础 63 个 .bin chunk + 16433 条 Entries
                 │
                 ├─ 组 ID 形如 11060203（Live2d），希格林德·余晖候令 = 11111103
                 └─ ExtraArtManifest(3171) 按 FileCount 顺序切进 47 个 slice
                          │
                          ▼
GET https://xonecn-hotupdatecdn.shziyi.com/release-cn/android/tags/major/2.4/
        classify/{Version}/classify/{Filename}
        Range: bytes={payload_off}-{payload_off+Size-1}
                          │
                          ▼
MD5 校验 → 存储态文件（单字节 XOR 的 UnityFS，key 可变，由首字节推导）
```

### 1.1 清单解密

- 反编译 `Framework.Files.FileSystemExtensions.ReadSecretBytes<object>`（RVA `0x3CE0A98`）→ `ReadSecretJson` → `ReadSecretText`；
- 引擎 = `Org.BouncyCastle.Crypto.Engines.TwofishEngine`（二进制里唯一的 BC 引擎），`PaddedBufferedBlockCipher`；
- 默认密钥来自 `KernelConst..cctor`（RVA `0x361C9CC`）：`Encoding.UTF8.GetBytes("D(G+KbPeShVmYq3t")` → `KernelConst.DefaultKey`；
- 复刻脚本：`DecryptSecret.java` + `bcprov-jdk18on-1.78.1.jar`（阿里云 Maven 镜像下载）。

### 1.2 URL 规则

`CollectChunks`（RVA `0x365DC68`）里的拼装：

```c
String.Format("{0}/{1}/classify/{2}", remoteRelativePath, Version, Filename)
// remoteRelativePath = "classify"，Version = PatchInfo.Version（如 2），Filename = res_xxx.slice
```

实测：
- `.../classify/2/classify/res_830046dbe3ec7dffc606d51b240b0389.slice` → **206**
- 完整下载 105,794,560 B，**MD5 = `830046db…` 与清单一致**
- 返回 302→40x.html 的都是不存在的路径（此前几十次探测失败皆因扩展名/前缀不对）

### 1.3 slice 容器格式（已逐条验证）

```
每条: [512B 头: 路径 ASCII + 定宽十进制字段 + 零填充][payload][补齐到 512]
条目总长 = roundup(512 + Size, 512)
payload 起点 = 条目起点 + 512
(payload = 清单里的存储态文件，MD5 == 清单 Hash)
```

- 本地完整 slice0 对 `BaseArtManifest` 前 88 条：**88/88 校验通过**（路径 + MD5）
- 文件尾多 1024 B 填充，不影响定位
- `ExtraArtManifest`(3171) 的 FileCount 之和 = 47 个 slice 的 FileCount 之和，顺序一一对应

### 1.4 按角色下载实测

`dl_by_character.py`：以同一份 chunk 清单做**定位 + Size + MD5 同源校验**（避免跨版本清单混用 Hash）：

```
=== 角色 Live2D 组 11060203 ===  9 个 bundle, 合计 23.13 MB
  OK  /assets/79/79988060cd53b64d.bundle  off=30376448  len=2057368  md5=387ee43f…
  OK  /assets/b9/b9fb3cd8401951b6.bundle  off=32434688  len=184440   md5=504862db…
  OK  /assets/0b/0b5c3b555d549067.bundle  off=32620032  len=74942    md5=1b29aff8…
  OK  /assets/6b/6b7e92dd492507ed.bundle  off=32695808  len=5228600  md5=22fc1198…
  OK  /assets/5e/5e908d530e849404.bundle  off=37925376  len=16523862 md5=d729a7fd…
  OK  /assets/ee/eecaa7efc1e2fa8b.bundle  off=54450176  len=4056     md5=d2bd1924…
  OK  /assets/bf/bfa995d79b489b4f.bundle  off=54454784  len=117915   md5=136eb1b0…
  OK  /assets/cd/cd8adb83b54e9f98.bundle  off=54573568  len=24336    md5=bc44e045…
  OK  /assets/b4/b4b251f0807f9ea0.bundle  off=54598656  len=38116    md5=67c901b3…
完成: 9 成功 / 0 失败
```

产物：`D:\codex\_ptn_cdn_research\dl_char_11060203\`（9 个存储态 bundle + 9 个解码 UnityFS，共 47 MB）。

> 注：此前一次 7/9 的失败是**跨版本清单混用 Hash**（用当前 `res_classify.json` 的 Hash 去校验 APK 内置旧切片的内容）。改为同源清单后 9/9 全过。

---

## 2. 边界与遗留

1. **新版本内容**：希格林德组 `11111103` 的 6 个 bundle 标记为 `Version=77`，**不在** APK 内置的基础切片（3171 条）里，也不在任一补丁 ZIP 的中央目录里（真实补丁链 21 段全部扫过，0 命中）。这类新增内容属于后续下发的增量切片，需要拿到对应版本的新 `res_base_classify_chunk.json` 才能定位；`res_classify.json`（3830 条）证明它们确实存在于当前版本。
2. **UnityPy 读取**：下载产物解码后是合法 UnityFS（头校验：magic/version=7/unityVersion/declared size 全部自洽），但本机 UnityPy 在 `decompress_lz4` 报 `Python int too large to convert to C long`（本地 lz4 库/Python 3.14 兼容问题），**与下载正确性无关**；项目已有 `tools/split_ptn_chunk.py` 的读取路径可复用。
3. **角色名映射**：组 ID `11060203` 未在本项目 `live2d_skin_map.json` 里，按铁律 5 **不猜中文名**，进 review 列表。
4. **合规**：全程仅 `xonecn-hotupdatecdn.shziyi.com` 的公开 GET（含 Range），无鉴权、无会话伪造。

---

## 3. 可复用产物（`D:\codex\_ptn_cdn_research`）

| 文件 | 作用 |
|---|---|
| `DecryptSecret.java` + `bcprov.jar` | Twofish 清单解密 |
| `dl_by_character.py` | 按角色组 → 同源清单定位 → Range 下载 → MD5 校验 |
| `verify_layout3.py` | slice 容器布局验证（88/88） |
| `demo_range_live2d.py` / `verify_char.py` | 过程验证脚本 |
| `DecompTargets.java` + `ghidra2/` + `jdk25/` | Ghidra headless 定向反编译（需 JDK 25） |
| `disasm2.py` / `find_xref.py` / `find_calls.py` | il2cpp 重定位解析反汇编工具链 |
| `decrypted_*.json` / `dec_*.json` | 已解密清单 |

## 4. 建议的下一步

1. 把「解密 + 索引 + Range 下载」收进 `plugins/sources/ptn_cdn.py`（纯公开 GET，符合铁律 3），只加一个 `ClassifySliceSource`；
2. 版本检查后从 CDN 拉取当前版 `res_base_classify_chunk.json`（若可公开取）以覆盖 v77 等新增内容；若不可公开取，则保留游戏代下作为兜底；
3. 组 ID → 中文装束名继续走「已核实才进 map，否则 review」的既有规则。
