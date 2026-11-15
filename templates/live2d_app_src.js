import { Application, Ticker } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display-lipsyncpatch/cubism4'
// 互动动作分类规则与预览端共用一份（core/interaction.py 是 Python 侧权威实现）
import {
  GREET as MOTION_GREET,
  STANDBY as MOTION_STANDBY,
  classifyMotion,
  buildInteractSeq as buildInteractSeqFromRules,
} from '../frontend/src/features/preview/motionRules.js'

// 布局统一模块由 wallpaper-layout.js 注入 window.WallpaperLayout
const WL = window.WallpaperLayout

// pixi.js v7 单包已自动注册 TickerPlugin 与 interaction 插件，无需手动 registerPlugin；
// Live2DModel 的 autoUpdate 需要知道用哪个 Ticker。
Live2DModel.registerTicker(Ticker)

let model = null
let app = null
let initW = 0
let initH = 0
let contentOffsetX = 0
let contentOffsetY = 0
let scale = 100
let ox = 0
let oy = 0
let alignment = 'center'
let meta = null
let lastMotionLabel = ''
let baseParameterValues = null
let basePartOpacities = null
let afterMotionUpdateHandler = null
// 背景层独立连续播放：官方 Background 动作是严格闭环（首末值完全相等）的循环，
// 平铺进各动作后结尾相位任意，切回 Idle 时背景会瞬跳。改为单独按时钟驱动。
let bgPlayer = null
let bgParamIndex = null
let fpsLimit = 60
let layoutControl = null
let materialTicker = null
// WE 面板开关（右侧属性面板）：鼠标追踪/互动/语音/台词/入场动画
let mouseTrackOn = true
let interactOn = true
let voiceOn = true
let subtitleOn = true
let introOn = true
// 互动与语音台词状态（数据在导出时注入 __L2D_VOICE_LINES）
let voiceLines = null
let voiceAudio = null
let voiceTxtTimer = 0
let standbyTimer = 0
let standbySwitchPending = false
let motionRequestSeq = 0
let interactionReady = false
let motionEntries = []
let interactSeq = []
let interactPtr = 0
let entryPlaying = false
let entrySequenceSeq = 0
let lastTapAt = 0
const linePtr = { greet: 0, touch: 0 }

function captureModelBaseline() {
  const coreModel = model?.internalModel?.coreModel
  const raw = coreModel?.getModel?.()
  if (!raw) return
  baseParameterValues = raw.parameters?.values ? new Float32Array(raw.parameters.values) : null
  basePartOpacities = raw.parts?.opacities ? new Float32Array(raw.parts.opacities) : null
  coreModel.saveParameters?.()
}

function restoreModelBaseline() {
  const coreModel = model?.internalModel?.coreModel
  const raw = coreModel?.getModel?.()
  if (!raw) return
  if (baseParameterValues && raw.parameters?.values) {
    raw.parameters.values.set(baseParameterValues)
  } else if (raw.parameters?.values && raw.parameters?.defaultValues) {
    raw.parameters.values.set(raw.parameters.defaultValues)
  }
  if (basePartOpacities && raw.parts?.opacities) {
    raw.parts.opacities.set(basePartOpacities)
  }
  coreModel.saveParameters?.()
}

function cubicAt(a, b, c, d, t) {
  const p01 = a + (b - a) * t
  const p12 = b + (c - b) * t
  const p23 = c + (d - c) * t
  const p012 = p01 + (p12 - p01) * t
  const p123 = p12 + (p23 - p12) * t
  return p012 + (p123 - p012) * t
}

// 与 Cubism 运行时 evaluateCurve 同语义：取第一个「结束时间 > time」的 segment 求值。
function evaluateSegments(raw, time, restricted) {
  let startTime = raw[0]
  let startValue = raw[1]
  let lastValue = startValue
  let cursor = 2
  while (cursor < raw.length) {
    const type = raw[cursor]
    if (type === 1) {
      const c1t = raw[cursor + 1]
      const c1v = raw[cursor + 2]
      const c2t = raw[cursor + 3]
      const c2v = raw[cursor + 4]
      const endTime = raw[cursor + 5]
      const endValue = raw[cursor + 6]
      if (endTime > time) {
        let t
        if (restricted) {
          t = endTime === startTime ? 0 : (time - startTime) / (endTime - startTime)
        } else {
          // 非 restricted：控制点时间参与参数化，二分求 x(t)=time（x 单调）。
          let low = 0
          let high = 1
          for (let i = 0; i < 24; i++) {
            const middle = (low + high) / 2
            if (cubicAt(startTime, c1t, c2t, endTime, middle) < time) low = middle
            else high = middle
          }
          t = (low + high) / 2
        }
        return cubicAt(startValue, c1v, c2v, endValue, Math.min(1, Math.max(0, t)))
      }
      startTime = endTime
      startValue = lastValue = endValue
      cursor += 7
    } else {
      const endTime = raw[cursor + 1]
      const endValue = raw[cursor + 2]
      if (endTime > time) {
        if (type === 0) {
          const t = endTime === startTime ? 0 : Math.max(0, (time - startTime) / (endTime - startTime))
          return startValue + (endValue - startValue) * t
        }
        // 2=stepped 保持前值，3=inverse stepped 直接取后值。
        return type === 2 ? startValue : endValue
      }
      startTime = endTime
      startValue = lastValue = endValue
      cursor += 3
    }
  }
  return lastValue
}

function findBackgroundMotion(modelUrl) {
  const groups = meta?.FileReferences?.Motions || meta?.Motions || {}
  for (const items of Object.values(groups)) {
    for (const item of items || []) {
      const file = item?.File || ''
      // 只匹配文件名本体：File 带 motions/ 目录前缀。
      const baseName = String(file).split(/[\\/]/).pop()
      if (/(?:^|_)background\.motion3\.json$/i.test(baseName)) {
        return new URL(file, new URL(modelUrl, window.location.href)).href
      }
    }
  }
  return ''
}

// 背景独立时钟：不再受动作切换影响，永远按 Background.motion3.json 的周期循环。
// 官方动作末帧与 Idle 首帧逐参数一致，人物本来无缝；唯一跳变来自我们平铺进
// 动作里的背景曲线相位，因此把背景从动作里剥出来单独驱动。
async function initBackgroundPlayer(modelUrl) {
  try {
    const url = findBackgroundMotion(modelUrl)
    if (!url) return
    const motion = await (await fetch(url)).json()
    const duration = Number(motion?.Meta?.Duration) || 0
    const curves = (motion?.Curves || []).filter(
      (curve) => curve.Target === 'Parameter' && Array.isArray(curve.Segments),
    )
    if (!duration || !curves.length) return
    bgPlayer = {
      curves,
      duration,
      restricted: motion?.Meta?.AreBeziersRestricted !== false,
      time: 0,
      lastNow: 0,
    }
    bgParamIndex = null
  } catch (e) {
    // 拿不到背景动作时保持原有行为（由动作自带曲线驱动）。
  }
}

function updateBackground(nowMs) {
  if (!bgPlayer || !model) return
  const coreModel = model.internalModel?.coreModel
  const raw = coreModel?.getModel?.()
  const parameters = raw?.parameters
  if (!parameters?.values) return
  if (!bgParamIndex) {
    bgParamIndex = new Map()
    const ids = parameters.ids
    if (ids && ids.length) {
      for (let index = 0; index < ids.length; index++) bgParamIndex.set(ids[index], index)
    } else {
      // Core 未暴露 ids 时退回框架的查表接口（一次性建立缓存）。
      for (const curve of bgPlayer.curves) {
        const index = coreModel.getParameterIndex?.(curve.Id)
        if (typeof index === 'number' && index >= 0) bgParamIndex.set(curve.Id, index)
      }
    }
  }
  // 掉帧/后台标签页恢复时钳制 dt，避免背景瞬移。
  const dt = bgPlayer.lastNow ? Math.min(0.1, Math.max(0, (nowMs - bgPlayer.lastNow) / 1000)) : 0
  bgPlayer.lastNow = nowMs
  bgPlayer.time = (bgPlayer.time + dt) % bgPlayer.duration
  for (const curve of bgPlayer.curves) {
    const index = bgParamIndex.get(curve.Id)
    if (index === undefined) continue
    parameters.values[index] = evaluateSegments(curve.Segments, bgPlayer.time, bgPlayer.restricted)
  }
  coreModel.saveParameters?.()
}

function queueStandbyTransition(requestSeq) {
  if (standbySwitchPending || requestSeq !== motionRequestSeq) return
  standbySwitchPending = true
  setTimeout(() => {
    if (requestSeq !== motionRequestSeq) {
      standbySwitchPending = false
      return
    }
    const standby = standbyLabel()
    if (standby) void playMotionByLabel(standby, { preservePose: true })
    else standbySwitchPending = false
  }, 0)
}

function watchMotionFinished() {
  updateBackground(performance.now())
  if (!model) return
  const manager = model.internalModel?.motionManager
  if (lastMotionLabel && !isStandbyMotion(lastMotionLabel)
      && manager && manager.playing === false && manager.isFinished?.()) {
    queueStandbyTransition(motionRequestSeq)
  }
}

function cleanPtnModelParts() {
  const im = model?.internalModel
  const coreModel = im?.coreModel
  const modelRaw = coreModel?.getModel?.()
  if (!modelRaw?.parts) return
  const parts = modelRaw.parts
  const count = parts.count || 0
  const partIds = parts.ids || []
  const opacities = parts.opacities
  if (!opacities) return
  // 仅隐藏辅助碰撞盒（hitarea）等非渲染图层，严禁使用 part\d+ 等通用流水号正则，避免误杀脸部五官等正常部件
  const hidePatterns = [/hitarea/i]
  for (let i = 0; i < count; i++) {
    const id = partIds[i] || ''
    if (hidePatterns.some((re) => re.test(id))) {
      opacities[i] = 0
    }
  }
}

function applyPtnRenderMaterials() {
  const materialIds = meta?.PTNRender?.MaterialIds
  const drawables = model?.internalModel?.coreModel?.getModel?.()?.drawables
  if (!Array.isArray(materialIds) || !drawables?.constantFlags || materialIds.length !== drawables.count) return
  const modes = [8, 0, 2, 0, 1, 9, 2, 1]
  for (let i = 0; i < drawables.count; i++) {
    const keep = drawables.constantFlags[i] & 4
    drawables.constantFlags[i] = keep | (modes[materialIds[i]] || 0)
  }
}

function measureDrawableContent() {
  const im = model?.internalModel
  const drawables = im?.coreModel?.getModel?.()?.drawables
  if (!drawables?.count) return null
  let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity
  for (let i = 0; i < drawables.count; i++) {
    if (!(drawables.dynamicFlags[i] & 1) || drawables.opacities[i] <= 0) continue
    const vertices = drawables.vertexPositions[i]
    if (!vertices) continue
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (let j = 0; j + 1 < vertices.length; j += 2) {
      const x = vertices[j], y = vertices[j + 1]
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue
      minX = Math.min(minX, x); maxX = Math.max(maxX, x)
      minY = Math.min(minY, y); maxY = Math.max(maxY, y)
    }
    // 过滤掉全屏背景大四边形/异常巨大特效：特征是「尺寸大 + 顶点数极少(≤16)」
    // 的 quad 网格（如 grandBackground/camera 特效）。模型本体的大网格（斗篷、
    // 裙摆等）顶点数通常几十上百，必须计入取景，否则包围盒偏小、布局放大后
    // 会导致下半身（躯干/腿）被裁出画布（诺克斯升阶、萦萦清涧照影的缺失根源）。
    const w = maxX - minX
    const h = maxY - minY
    const vertexCount = vertices.length / 2
    if ((w > 50 || h > 50) && vertexCount <= 16) continue
    left = Math.min(left, minX)
    right = Math.max(right, maxX)
    top = Math.min(top, minY)
    bottom = Math.max(bottom, maxY)
  }
  if (!Number.isFinite(left) || right <= left || bottom <= top) return null
  const ppu = im.pixelsPerUnit || 1
  return {
    width: (right - left) * ppu,
    height: (bottom - top) * ppu,
    centerX: ((left + right) / 2) * ppu,
    centerY: ((top + bottom) / 2) * ppu,
  }
}

function applyLayout() {
  if (!model || !initW || !initH) return
  const a = WL.anchor(alignment)
  const out = WL.l2dLayout(initW, initH, window.innerWidth, window.innerHeight, {
    scale,
    offsetX: ox,
    offsetY: oy,
    alignX: a.x,
    alignY: a.y,
  })
  model.scale.set(out.sx)
  model.position.set(out.x - contentOffsetX * out.sx, out.y + contentOffsetY * out.sx)
}

// 鼠标追踪：鼠标移动时让角色视线/头部跟随（受 WE 面板「鼠标追踪」开关控制）
function onMouseMove(e) {
  if (!mouseTrackOn || layoutControl?.state.adjustSize || !model) return
  model.focus(e.clientX, e.clientY)
}

function syncInputMode() {
  const adjusting = !!layoutControl?.state.adjustSize
  if (!mouseTrackOn || adjusting) {
    try {
      model?.internalModel?.focusController?.focus(0, 0, true)
    } catch (e) { /* 模型未就绪时忽略 */ }
  }
}

// 切换鼠标追踪：关闭或调整位置时把视线/头部参数归零
function setMouseTrack(on) {
  mouseTrackOn = on
  syncInputMode()
}

function applyFpsLimit(value) {
  const fps = Number(value)
  fpsLimit = Number.isFinite(fps) ? Math.min(200, Math.max(15, fps)) : 60
  if (app) app.ticker.maxFPS = fpsLimit
}

// 收集 model3.json 里全部动作条目：[{ group, stem }]（stem 为动作文件名去 .motion3.json）
function collectMotionEntries(motions) {
  const entries = []
  const seenFiles = new Set()
  const groups = Object.keys(motions || {}).sort((a, b) => {
    if (a === 'All') return -1
    if (b === 'All') return 1
    return 0
  })
  for (const group of groups) {
    for (let index = 0; index < (motions[group] || []).length; index++) {
      const file = motions[group][index].File || ''
      const identity = file.replace(/\\/g, '/').toLocaleLowerCase()
      if (!file || seenFiles.has(identity)) continue
      seenFiles.add(identity)
      const stem = file.split('/').pop().replace(/\.motion3\.json$/i, '')
      if (stem) entries.push({ group, index, file, stem })
    }
  }
  return entries
}

function buildInteractSeq(entries) {
  // 规则见 frontend/src/features/preview/motionRules.js：
  // Boring / Level 各触发一次，Click_Head 固定配摸头，其余 Click_* 按文件顺序
  // 依次对应普通触摸1..N。preferred 命中时按 action 锁定台词，未命中按池内轮换。
  // 导出器会按实际 model3.json 预先注入 __L2D_INTERACT_SEQ（Python 权威实现）；
  // 没有注入（旧壁纸工程）时才用同一套 JS 规则现算。
  const injected = Array.isArray(window.__L2D_INTERACT_SEQ) ? window.__L2D_INTERACT_SEQ : null
  if (injected) {
    const labels = new Set(entries.map((entry) => entry.stem))
    interactSeq = injected
      .filter((step) => step && labels.has(step.label))
      .map((step) => ({
        label: String(step.label),
        category: String(step.category || 'touch'),
        preferred: String(step.preferred || ''),
      }))
  } else {
    interactSeq = buildInteractSeqFromRules(
      entries.map((entry) => entry.stem),
      isStage2Interact(),
      !!voiceLines?.head_click_interact,
    )
  }
  interactPtr = 0
}

function standbyLabel() {
  // 只认模型声明的待机动作。不能回落到 motionEntries[0]：零 Idle/Background 的
  // 模型（如 Hestia_4）宁可停住，也不能把排在前面的 Boring/Click 当待机播。
  return (motionEntries.find((entry) => isStandbyMotion(entry.stem)) || {}).stem || ''
}

function stopVoiceAudio() {
  if (voiceAudio) {
    try { voiceAudio.pause() } catch (e) {}
    voiceAudio = null
  }
  if (voiceTxtTimer) {
    clearTimeout(voiceTxtTimer)
    voiceTxtTimer = 0
  }
}

function hideVoiceText() {
  const el = document.getElementById('voiceTxt')
  if (el) el.style.opacity = '0'
}

// 台词 action 精确匹配优先，其次前缀匹配（兼容「摸头-男/女」「普通触摸2-女」等变体）。
function findVoiceLine(pool, action) {
  if (!action) return null
  return pool.find((item) => item.action === action)
    || pool.find((item) => String(item.action || '').startsWith(action))
    || null
}

// 摸头只由 Click_Head 固定触发，不能混进普通触摸的顺序轮换。
// 二阶段互动同理：互动1 固定由入场 Greet 播放，Click 轮换只从互动2 开始。
function rotationPoolFor(category, pool) {
  if (category === 'touch') {
    return pool.filter((item) => !String(item.action || '').startsWith('摸头'))
  }
  if (category === 'interact') {
    return pool.filter((item) => !String(item.action || '').startsWith('互动1'))
  }
  return pool
}

// 四星皮二阶段判据：台词库带非空 interact 池（互动1-5）。
function isStage2Interact() {
  return !!(voiceLines?.interact?.length)
}

// 二阶段语义下的待机判定：Exitidle 在二阶段是互动退出动作（播完要回 Idle），
// 不能按一阶段语义当成待机，否则 Loop=true 的它永远不会回待机。
function isStandbyMotion(label) {
  return classifyMotion(label, isStage2Interact()) === MOTION_STANDBY
}

function playLine(category, preferredAction = '') {
  let pool = voiceLines?.[category]
  // Boring 动作读「闲置」台词；部分装束（133 个只有普通触摸1/2+特别关注）没有闲置条目，
  // 此时回落到 boring 池的「烦躁」，避免互动动作完全没有语音。
  if ((!pool || !pool.length) && category === 'idle') pool = voiceLines?.boring
  if (!pool?.length) return
  let entry = findVoiceLine(pool, preferredAction)
  if (!entry) {
    // 摸头没有对应台词时不拿普通触摸顶替（宁可不说话，也不串台）。
    if (preferredAction === '摸头') return
    const usable = rotationPoolFor(category, pool)
    if (!usable.length) return
    const index = (linePtr[category] || 0) % usable.length
    linePtr[category] = index + 1
    entry = usable[index]
  }
  stopVoiceAudio()
  const textEl = document.getElementById('voiceTxt')
  const wantVoice = voiceOn && entry.audio
  const wantText = subtitleOn && entry.text
  if (!wantVoice && !wantText) return
  let audioDone = !wantVoice
  const finishText = () => {
    if (audioDone && textEl) textEl.style.opacity = '0'
  }
  if (wantText && textEl) {
    textEl.textContent = entry.text
    textEl.style.opacity = '1'
  }
  if (wantVoice) {
    try {
      const audio = new Audio(entry.audio)
      audio.volume = 0.95
      audio.onended = () => {
        stopVoiceAudio()
        audioDone = true
        finishText()
      }
      voiceAudio = audio
      audio.play().catch(() => {
        stopVoiceAudio()
        audioDone = true
        finishText()
      })
    } catch (e) {
      audioDone = true
    }
  }
  if (!wantVoice) {
    const duration = Math.min(7000, Math.max(2500, 1800 + (entry.text || '').length * 130))
    voiceTxtTimer = setTimeout(finishText, duration)
  }
}

async function startEntrySequence() {
  const greet = motionEntries.find((entry) => classifyMotion(entry.stem) === MOTION_GREET)
  if (!greet) return
  const entrySeq = ++entrySequenceSeq
  entryPlaying = true
  const started = await playMotionByLabel(greet.stem)
  if (!started || entrySeq !== entrySequenceSeq || !entryPlaying) {
    if (entrySeq === entrySequenceSeq) entryPlaying = false
    return
  }
  // 二阶段：入场 Greet 播互动1；一阶段播问候
  if (isStage2Interact()) playLine('interact', '互动1')
  else playLine('greet', '问候')
}

function activeMotionLabel() {
  const state = model?.internalModel?.motionManager?.state
  if (!state) return lastMotionLabel
  const active = motionEntries.find(
    (entry) => entry.group === state.currentGroup && entry.index === state.currentIndex,
  )
  return active?.stem || lastMotionLabel
}

async function interruptEntryToStandby() {
  entrySequenceSeq += 1
  entryPlaying = false
  stopVoiceAudio()
  hideVoiceText()
  const standby = standbyLabel()
  // 主动点击中断必须立即切 Idle；自然结束才使用 240ms 姿态混合。
  if (standby) await playMotionByLabel(standby)
  stopVoiceAudio()
  hideVoiceText()
}

async function startInteractSequence() {
  const now = Date.now()
  if (!interactionReady || now - lastTapAt < 600) return
  lastTapAt = now
  if (classifyMotion(activeMotionLabel()) === MOTION_GREET) {
    await interruptEntryToStandby()
    return
  }
  if (!interactSeq.length) return
  const step = interactSeq[interactPtr % interactSeq.length]
  interactPtr += 1
  stopVoiceAudio()
  hideVoiceText()
  const started = await playMotionByLabel(step.label)
  if (started) playLine(step.category, step.preferred)
}

async function start() {
  const cfg = window.__L2D_CONFIG
  const init = window.__L2D_INIT || {}
  scale = WL.clampScale(init.scale != null ? init.scale : 100)
  ox = WL.clampOffset(init.ox != null ? init.ox : 0)
  oy = WL.clampOffset(init.oy != null ? init.oy : 0)
  alignment = init.alignment || 'center'
  layoutControl = WL.layoutController(document.getElementById('canvas'), {
    scale,
    offsetX: ox,
    offsetY: oy,
    alignment,
    storageKey: init.storageKey || '',
    onChange: (state) => {
      scale = state.scale
      ox = state.offsetX
      oy = state.offsetY
      alignment = state.alignment
      applyLayout()
    },
    onModeChange: syncInputMode,
  })
  scale = layoutControl.state.scale
  ox = layoutControl.state.offsetX
  oy = layoutControl.state.offsetY
  alignment = layoutControl.state.alignment
  const wantAnim = init.anim || ''
  mouseTrackOn = init.track !== false
  interactOn = init.interact !== false
  voiceOn = init.voice !== false
  subtitleOn = init.subtitle !== false
  introOn = init.intro !== false
  voiceLines = window.__L2D_HAS_VOICE_LINES ? (window.__L2D_VOICE_LINES || null) : null

  app = new Application({
    view: document.getElementById('canvas'),
    backgroundAlpha: 0,
    autoStart: true,
    resizeTo: window,
    antialias: true,
    // 后台标签页 rAF 被节流时 canvas 读回会被清空；调试/无头抓帧可打开。
    preserveDrawingBuffer: !!(window.__L2D_INIT && window.__L2D_INIT.preserveDrawingBuffer),
  })
  applyFpsLimit(fpsLimit)
  model = await Live2DModel.from(cfg.model, {
    autoUpdate: true,
    ticker: app.ticker,
    motionPreload: 'ALL',
    // 关闭库自带的鼠标追踪/命中（autoHitTest 会监听 pointermove 并自动 focus），
    // 改为自定义 mousemove 实现，以便 WE 面板「鼠标追踪」开关能实时开/关。
    // （0.5.0 起 autoInteract 拆分为 autoHitTest + autoFocus）
    autoHitTest: false,
    autoFocus: false,
  })
  // 调试钩子：无头/后台标签页 rAF 被节流时，可手动 update+render 抓帧。
  window.__L2D_APP__ = app
  // 新版提取工具导出的 Texture2D 已由 AssetStudio/ImageSharp 做过标准正向转换，
  // 强制 textureFlipY = true 会把正常图片再次上下颠倒，导致网格 UV 错位成碎片。
  // model.internalModel.textureFlipY = true
  cleanPtnModelParts()
  captureModelBaseline()
  afterMotionUpdateHandler = () => watchMotionFinished()
  model.internalModel?.on?.('afterMotionUpdate', afterMotionUpdateHandler)
  model.anchor.set(0.5, 0.5)
  window.__L2D_MODEL__ = model
  app.stage.addChild(model)
  // pixi-live2d-display 会忽略动作自带的 Loop 标志，动作播完会自动随机切 Idle 姿势。
  // 这里禁用库的自动随机 Idle：壁纸只会一直播放当前动作；循环与否由动作自带 Meta.Loop 决定。
  const motionManager = model.internalModel?.motionManager
  if (motionManager) {
    motionManager.groups.idle = '__none__'
    motionManager.on('motionStart', (group, index) => {
      const motion = motionManager.motionGroups?.[group]?.[index]
      // PTN 的人物动作常被统一标记 Loop=true；只有 Background/Idle 待机动作循环。
      const entry = motionEntries.find((item) => item.group === group && item.index === index)
      const autoLoop = !!entry && isStandbyMotion(entry.stem)
      if (motion && typeof motion.setIsLoop === 'function') {
        motion.setIsLoop(autoLoop)
        if (typeof motion.setIsLoopFadeIn === 'function') motion.setIsLoopFadeIn(false)
        if (typeof motion.setFadeInTime === 'function') motion.setFadeInTime(0)
        if (typeof motion.setFadeOutTime === 'function') motion.setFadeOutTime(0)
      }
    })
  }
  const content = measureDrawableContent()
  if (content) {
    initW = content.width
    initH = content.height
    contentOffsetX = content.centerX
    contentOffsetY = content.centerY
  } else {
    initW = model.width
    initH = model.height
  }
  applyLayout()
  // 鼠标追踪：视线/头部跟随鼠标
  window.addEventListener('mousemove', onMouseMove)
  // 点击互动：顺序播放除 Idle/Background/Greet 外的动作。
  // interactionReady 只在模型元数据、动作列表与预加载全部完成后打开。
  window.addEventListener('click', () => {
    if (!model || !meta || layoutControl?.state.adjustSize || !interactOn) return
    void startInteractSequence()
  })
  try {
    meta = await (await fetch(cfg.model)).json()
    applyPtnRenderMaterials()
    void initBackgroundPlayer(cfg.model)
    // PTN 的反向遮罩/混合模式位来自 Unity Material，模型更新可能改写 flags；
    // 在共享 ticker 上持续恢复，确保与动画更新、渲染使用同一限帧节奏。
    materialTicker = () => applyPtnRenderMaterials()
    app.ticker.add(materialTicker)
    motionEntries = collectMotionEntries(meta?.FileReferences?.Motions || meta?.Motions || {})
    buildInteractSeq(motionEntries)
    // motionPreload='ALL' 已发起加载；显式等待全部完成后才允许入场/点击，避免异步竞态。
    await Promise.all(motionEntries.map((entry) => motionManager?.loadMotion?.(entry.group, entry.index)))
    interactionReady = true
    if (wantAnim && motionEntries.some((entry) => entry.stem === wantAnim)) {
      await playMotionByLabel(wantAnim)
    } else if (introOn && motionEntries.some((entry) => classifyMotion(entry.stem) === MOTION_GREET)) {
      await startEntrySequence()
    } else {
      const standby = standbyLabel()
      if (standby) await playMotionByLabel(standby)
    }
  } catch (e) { /* 无动作不影响显示 */ }
}

// 按动作文件名播放：预加载后 FORCE 启动，并用递增令牌淘汰过期请求。
// 只有 Background/Idle 可手动循环；自动待机优先 Idle，其余动作完成后回 Idle。
async function playMotionByLabel(label, { preservePose = false } = {}) {
  if (!label || !meta || !model) return false
  const hit = motionEntries.find((entry) => entry.stem === label)
  if (!hit) return false
  const requestSeq = ++motionRequestSeq
  if (standbyTimer) {
    clearTimeout(standbyTimer)
    standbyTimer = 0
  }
  standbySwitchPending = false
  try {
    const im = model.internalModel
    const manager = im?.motionManager
    const loaded = await manager?.loadMotion?.(hit.group, hit.index)
    if (requestSeq !== motionRequestSeq || !loaded) return false
    // 主动切换动作时恢复基线；自然结束回待机走 preservePose：
    // 官方动作末帧与 Idle 首帧逐参数一致，恢复基线反而让不驱动的参数跳一下。
    // 背景参数由独立时钟每帧覆盖，不受这里影响。
    if (!preservePose) restoreModelBaseline()
    manager?.stopAllMotions?.()
    const isStandby = isStandbyMotion(label)
    if (isStandby) entryPlaying = false
    const returnToStandby = () => {
      if (requestSeq !== motionRequestSeq || isStandby) return
      queueStandbyTransition(requestSeq)
    }
    const started = await model.motion(hit.group, hit.index, 3, {
      onFinish: returnToStandby,
    })
    if (requestSeq !== motionRequestSeq || !started) return false
    lastMotionLabel = label
    if (!isStandby) {
      const duration = loaded?._motionData?.duration || 10
      standbyTimer = setTimeout(returnToStandby, duration * 1000 + 500)
    }
    return true
  } catch (e) {
    return false
  }
}

// WE 首次推送用于恢复该壁纸路径下保存的布局与用户偏好；后续推送实时生效。
// layoutController 在稳定 localStorage 布局存在时保留它，否则按首推中实际存在的字段采纳，
// 不假定首推一定包含 alignment。
window.wallpaperPropertyListener = {
  applyUserProperties: function (properties) {
    try {
      if (properties.targetfps) applyFpsLimit(properties.targetfps.value)
      if (layoutControl) layoutControl.applyUserProperties(properties)
      if (properties.mousetrack) setMouseTrack(!!properties.mousetrack.value)
      if (properties.interact) interactOn = !!properties.interact.value
      if (properties.voice) {
        voiceOn = !!properties.voice.value
        if (!voiceOn) stopVoiceAudio()
      }
      if (properties.subtitle) {
        subtitleOn = !!properties.subtitle.value
        if (!subtitleOn) hideVoiceText()
      }
      if (properties.intro) introOn = !!properties.intro.value
      if (model) applyLayout()
    } catch (e) {}
  },
}

window.__captureCanvasDataURL = function () {
  try {
    return document.getElementById('canvas').toDataURL('image/png')
  } catch (e) {
    return ''
  }
}

window.addEventListener('resize', () => { if (model) applyLayout() })
start().catch((e) => { document.title = 'ERR ' + e.message })
