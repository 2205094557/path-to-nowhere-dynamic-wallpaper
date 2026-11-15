# 无期迷途动态壁纸工坊

Path to Nowhere（无期迷途）立绘提取与 Wallpaper Engine 动态壁纸工具。

从游戏官方热更 CDN 与模拟器本地资源中定位并提取 **Live2D Cubism 4 立绘**（moc3 / 贴图 / 动作 / 材质）与静态立绘，
在本地 Web 图鉴中预览、配语音台词与点击互动，一键导出为可交互的 Wallpaper Engine 壁纸。

![characters](https://img.shields.io/badge/characters-163-4a90d9)
![skins](https://img.shields.io/badge/skins-326-4a90d9)
![voice](https://img.shields.io/badge/voice%20skins-313-4a90d9)
![tests](https://img.shields.io/badge/tests-96-brightgreen)

## 特性

### 图鉴与预览

- **163 名角色 / 326 个装束**（325 Live2D + 1 静态），图鉴数据由 BWiki 同步
- 筛选维度：职业 / 稀有度 / 星级 / 装束系列 / 动态能力 / 收藏，关键词搜索角色名与装束名
- Live2D 实时预览：鼠标追踪视线、动作互动、入场动画、缩放/偏移/对齐、背景自动取色
- **点击互动语义还原游戏**：`Greet` 入场问候、`Click_Head` 摸头、其余 `Click_*` 按模型动作顺序依次对应普通触摸 1..N
- **四星互动皮二阶段**：`Greet` 播互动1，其余 `Click_*` 顺序播互动2..5，`Exitidle` 播退出互动并自动回到待机
- **语音与字幕**：BWiki 语音页同步 2663 条台词与 mp3，点击动作自动播放配套语音并显示台词；语音/文本开关独立

### 资源获取

- 官方 CDN 下载：classify 分组通道（明文清单 + Range 续传）与 chunk 通道双路，支持断点续传、取消、SSE 实时进度
- 支持两代资源容器：旧版 50B 前缀 + 单字节 XOR 的 UnityFS v7；2026-11 起的 50B 明文头 + 明文 UnityFS v8（64 字节对齐、前缀块与裸块混排）
- 模拟器资源扫描装配：直接读取 MuMu 共享目录（`fs` / `fsclassify` / `fschunk`），按内容扫描定位不在清单中的装束
- 四星皮两阶段独立包（`<internal>_<seq>` / `<internal>_<seq>01`）各自装配，moc 与贴图强制同源

### 壁纸导出

- 一键生成 Wallpaper Engine 工程并应用，标题按实际能力拼接（互动 / 语音）
- 布局契约：稳定的分类型布局键、默认关闭的「调整位置」左键拖动、`applyUserProperties` 首推恢复
- Live2D 独立帧率（默认 60，15~200）、模型更新与渲染共享 ticker
- 导出封面：按内容包围盒完整取景并重绘同色背景，WE 图鉴直接显示完整立绘
- **AI 超分**：Waifu2x / Real-CUGAN / Real-ESRGAN（NCNN Vulkan），Live2D 贴图与静态立绘整体放大，Alpha 保护，非破坏多版本，预览热切换、导出联动

### 动作处理工具

- `tools/merge_background_motions.py`：把官方背景层曲线平铺合并进所有人物动作，表情/互动时背景同步动效
- `tools/smooth_stepped_motions.py`：把 30Hz 阶梯关键帧动作转线性插值（部分模型原生为 stepped，60fps 下观感像 30fps）
- `tools/normalize_motion_tail.py`：合并动作短层尾部静止补长

## 技术栈

- 后端：Python 3.12（标准库 HTTP 服务）+ UnityPy
- 前端：Vue 3 + Vite + naive-ui + pixi-live2d-display（Cubism 4）
- 桌面壳：pywebview
- 数据源：无期迷途 BWiki（图鉴 / 语音 / 静态立绘）、官方热更 CDN（chunk 资源）

## 快速开始（源码版）

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend && npm install && cd ..

start_dev.bat            :: 后端 8766 + Vite 5173 + 桌面窗口
start_dev.bat --stop     :: 一键关闭整套 dev 栈
```

仓库内含 BWiki 元数据（`resources/metadata/`，约 3.8MB），克隆后无需联网即可浏览图鉴；
点击「下载」时才访问官方 CDN 拉取该装束的资源。

语音音频不随仓库分发，执行 `python tools/sync_voice_lines.py` 从 BWiki 补齐（限速 2s/请求）；
纯文本台词预览不受影响。

## 打包

```bat
python scripts/build_pack.py
```

产物：`dist/ptn-wallpaper/ptn-wallpaper.exe`（内置后端 8770 + 静态前端 5174）。

## 目录结构

```
backend_server.py        本地 API（图鉴 / 下载 / 提取 / 导出 / 取色 / 语音）
core/                    服务层：插件注册、资源库、元数据、壁纸生成、WE 集成、AI 超分、语音目录、点击互动规则
plugins/sources/         数据源：BWiki、PTN CDN（classify + chunk 双通道）
plugins/extractors/      提取器：静态 Texture2D / Sprite、Live2D Cubism
plugins/exporters/       导出器：静态壁纸、Live2D 壁纸
templates/               导出模板：布局脚本、Live2D 运行时
frontend/                Vue 图鉴 / 预览 / 设置
tools/                   资源定位、装配、图鉴与语音同步、动作处理、验收脚本
docs/                    CDN 通道与提取流程的研究记录
tests/                   规则与解析回归测试
```

### 关键实现约定

- `templates/wallpaper-layout.js` 是导出壁纸布局的单一事实来源
- `core/interaction.py` 是点击互动语义的 Python 权威实现，`frontend/src/features/preview/motionRules.js` 为等价 JS 版；
  二者由 `tests/test_interaction.py` 与 `tests/motion_rules.test.mjs` 用同一组用例做全量 diff 校验
- 修改 `templates/live2d_app_src.js` 后必须重建 `templates/live2d-app.js`：

  ```bat
  node frontend/node_modules/vite/bin/vite.js build --config frontend/vite.live2d.config.mjs
  ```

## 测试

```bat
python -I -m unittest discover -s tests -p "test_*.py"
node tests/motion_rules.test.mjs
```

覆盖动作解析/装配、点击互动规则（含四星二阶段）、语音模板解析、超分产物选择、导出器序列注入等；
JS 侧会调 Python 子进程对全部已提取模型逐条比对互动序列。

## 已知限制

- 与游戏原生 Unity 渲染相比，少量多层特效仍可能有差异（PTN 使用 `CubismRenderControllerV2`、时间轴分层动作与多层特效）
- 部分装束的 BWiki 语音页缺少音频文件或上传主体名与角色名不一致，会退化为纯台词
- 4x 超分产物（16384²）超过部分 WebGL 纹理上限，预览建议用 2x
- 极少数模型的 `model3.json` 动作清单比磁盘文件少，点击序列按清单轮换

## 声明

本项目仅访问游戏公开分发通道与公开 Wiki，用于个人壁纸用途；不绕过任何鉴权，不修改游戏客户端。
所有游戏素材版权归官方所有，请勿用于商业用途。
