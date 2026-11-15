# AGENTS.md

无期迷途立绘提取/壁纸导出工具（复刻同架构前身项目的分层设计：core 服务层 + plugins 插件层 + Vue 前端）。

## 铁律

1. `D:\codex` 下的前身参考仓库（与本仓库同构的另一款壁纸工具）是**只读参考**：不 import、不修改、不把输出写进它的 resources；在本仓库的任何文件里都不出现它的名称与品牌词。
2. 本项目不出现任何其他游戏的项目元素：其图鉴维度（阵营/舰种类）、其资源目录命名、其 wiki 代号、其专用工具链等一律不得引入；角色/装束语义统一用 character/skin 与 PTN 字段（profession/rarity/series/grade）。
3. CDN 交互仅限公开 GET（xonecn-hotupdatecdn.shziyi.com / wiki.biligame.com），不实现鉴权绕过；BWiki 有限流（实测 HTTP 567/429），同步必须限速 ≥1s/请求并带退避。
4. 资源容器有**两代格式**，解析必须都支持：
   - 旧版：50B 前缀 + 单字节 XOR 的 UnityFS v7 (Unity 2019.4.40f1)；
   - 2026-11-11 更新后：50B **明文**头 + 明文 UnityFS **v8** (Unity 2022.3.62f3)，块间 **64 字节对齐**，且「带 50B 前缀」与「裸 UnityFS」两种块会混排。
   解析入口：`tools/split_ptn_chunk.py`（旧）与 `tools/export_from_emulator.py::iter_chunk_bodies`（新旧混合）。
   判断依据（与社区公开脚本一致）：UnityFS 头里的 `cbi/ubi != 0xFFFFFFFF` 才是真 bundle；`size` 在 body+30 处按大端 u64 读取，下一条偏移 = `align64(body + size)`。
5. BWiki 图鉴与 CDN 资源的对应关系以“装束名/角色名”为一级匹配键，无法自动映射的进 review 列表，不许静默猜。

## 经验教训

通用工程条目（GBK 控制台、原子写、限流退避等）沿用前身项目沉淀的实践，落地时写进本项目文档，不跨仓库引用文件。

### 资源定位（2026-11-12 踩坑总结）

1. **不要用「清单缺失」或「文件名 grep 无命中」断定资源不存在**：
   - `classify_group_map.json` / `classify_index.json` 只代表客户端已登记的分组，不等于磁盘文件；
   - 新容器里的 AssetBundle 名常被压缩，明文 grep 会漏；负结果不可信。
2. 资源定位以**内容扫描**为准：用 UnityPy 逐个打开 bundle / chunk 块，读 `AssetBundle.m_Name` 与 moc 的 `MonoBehaviour.m_Name`。
3. 宿主机可直接读模拟器共享目录 `D:/文档/MuMu共享文件夹/files`（`fs` / `fsclassify` / `fschunk`，实测约 15GB），优先用它，不必走 adb。
4. 设备 chunk 集合会随版本**整体更换**（实测 2026-11-11 更新后 75 个 chunk 与旧索引的 63 个完全不重名），旧 chunk 清单/偏移一律作废，须以当前目录与内容扫描为准。
5. 四星皮多为**两阶段两套独立包**：`<internal>_<seq>`（阶段一）与 `<internal>_<seq>01`（阶段二），各自有独立 moc / 贴图 / 动作；不要把同一包内的多张贴图误判成阶段。
6. 装配时 moc 与贴图必须来自**同一构建**：实测混用两套资源的贴图差异可达 78.9%，渲染会碎片化/黑块。
7. 设备三个目录都要搜：`fs/assets`（明文资产，moc/贴图/prefab/动画）、`fschunk/*.bin`、`fsclassify/assets`。**不在这三个里搜完，不得断言设备上没有该资源**（瑟琳·微明止息只在 `fs/assets`）。
8. 不在 classify 303 组内的装束（如 `shalom_6`/`shalom_601`）走 classify 通道会报「设备清单无该组」，这是**通道不对**而非无资源；必须改用 `--scan-dir` 内容扫描装配。
9. moc 容器带 UnityFS flags `0x3000`（UnityCN 加密标志）时 UnityPy 读不了，XOR `read_block` 也失败；moc 数据 = `raw[128 : raw.rfind(b"UnityFS")]` 直接切片。
10. 切皮肤后若渲染的还是上一个模型（无报错），先确认 `window.__L2D_MODEL__.internalModel.settings.url`；同时检查是否有**多套 dev 进程并存**导致元数据请求落到 `/resources/metadata/*`（HTML 404）而非 `/@fs/...`。清理：`python -I scripts/start_dev.py --stop` 后重启。

## 框架关系

本项目派生自 `D:\codex\dynamic-wallpaper-framework`（通用层：core 服务层、壁纸导出、
WE 集成、前端壳）。通用 bug 与通用功能改进请改框架本体，再同步回本项目；
本项目的游戏专属层（plugins/sources、plugins/extractors、tools 图鉴同步、前端筛选维度）
不回灌框架。新开其他游戏壁纸项目时读框架 AGENTS.md 的推导清单。

## Wallpaper Engine 通用契约

1. `templates/wallpaper-layout.js` 是导出壁纸布局的单一事实来源；Live2D 与静态导出都必须复制该脚本并注入按类型隔离的稳定 `layout_storage_key`。
2. 三类壁纸属性包含默认关闭的「调整位置」：开启时布局脚本仅接管左键拖动，关闭后定格；Live2D 调整时暂停鼠标追踪并把视线/头部归中。
3. `applyUserProperties` 首次推送用于恢复 WE 按路径保存的属性：有 localStorage 稳定布局时优先保留，否则采纳首推里实际存在的布局字段（首推不保证包含 alignment），不得整次跳过。
4. Live2D 的 `targetfps` 默认 60、范围 15~200；模型更新和渲染必须共享 `app.ticker`，`Live2DModel.from` 显式传 `ticker: app.ticker`。
5. `templates/live2d_app_src.js` 改动后必须运行 `node frontend/node_modules/vite/bin/vite.js build --config frontend/vite.live2d.config.mjs` 重建 `templates/live2d-app.js`。
6. 同步框架通用能力时，Live2D 源码必须定点合并，保留 PTN 的辅助 drawable 清理、反向遮罩与 Material 混合、实际 drawable 包围盒、中心偏移、点击互动和材质持续应用；两个 exporter 必须保留超分变体选择与清理逻辑。
7. 导出项目必须写真实存在的 `preview.jpg` 并在 `project.json` 引用：WE 找不到 preview 会持续报 `icon create err`。Live2D 封面由前端按内容包围盒完整取景截取、后端按导出背景样式重绘背景合成（不能用当前缩放/偏移后的视口，也不能直接用平铺贴图集）；静态装束用立绘原图。无截图上下文（CLI/导出器冒烟）必须生成同色系兜底封面。
