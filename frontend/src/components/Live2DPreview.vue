<template>
  <div ref="wrapRef" class="l2d-wrap">
    <canvas ref="canvasRef"></canvas>
    <transition name="bubble-fade">
      <div v-if="subtitle" class="l2d-subtitle-box">
        <span class="subtitle-txt">{{ subtitle }}</span>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { Application, Ticker } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display-lipsyncpatch/cubism4'
import { assetUrl, bridge } from '../bridge'
// 互动动作分类规则：与导出壁纸运行时共用（Python 侧权威实现在 core/interaction.py）
import {
  GREET as MOTION_GREET,
  STANDBY as MOTION_STANDBY,
  classifyMotion,
  buildInteractSeq as buildInteractSeqFromRules,
} from '../features/preview/motionRules.js'
import '../../../templates/wallpaper-layout.js'

const WL = window.WallpaperLayout

// pixi.js v7 单包已自动注册 TickerPlugin 与 interaction 插件，无需手动 registerPlugin；
// Live2DModel 的 autoUpdate 需要知道用哪个 Ticker。
Live2DModel.registerTicker(Ticker)

const props = defineProps({
  skin: { type: Object, required: true },
  // 超分版本衍生 model3.json 文件名（如 Adela_2_waifu2x_2x.model3.json）：
  // 为空时用 skin.asset.model 原版；切换版本由父级通过 :key 重挂载完成热重载
  modelFile: { type: String, default: '' },
  animation: { type: String, default: '' },
  scale: { type: Number, default: 100 },
  offsetX: { type: Number, default: 0 },
  offsetY: { type: Number, default: 0 },
  alignment: { type: String, default: 'center' },
  // 鼠标追踪开关：角色视线/头部跟随鼠标（与壁纸端 WE 面板开关一致）
  mouseTrack: { type: Boolean, default: true },
  // 互动动作开关：点击立绘按顺序播放互动动作并配台词
  interact: { type: Boolean, default: true },
  // 语音开关：控制语音音频播放
  voice: { type: Boolean, default: true },
  // 文本开关：控制台词框显示（与语音互相独立）
  subtitles: { type: Boolean, default: true },
  // 入场动画开关：进入立绘时播放 Greet → 问候台词 → Background 待机
  introOn: { type: Boolean, default: true },
})
const emit = defineEmits(['ready', 'error', 'animations', 'scaleChange', 'panChange'])

const wrapRef = ref(null)
const canvasRef = ref(null)
const subtitle = ref('')

let app = null
let model = null
let motionList = []
let initW = 0
let initH = 0
let contentOffsetX = 0
let contentOffsetY = 0
let drag = null
let currentLabel = ''
let baseParameterValues = null
let basePartOpacities = null
let afterMotionUpdateHandler = null
// 背景层独立连续播放：官方 Background 动作是严格闭环（首末值完全相等）的循环，
// 平铺进各动作后结尾相位任意，切回 Idle 时背景会瞬跳。改为单独按时钟驱动。
let bgPlayer = null
let bgParamIndex = null
let disposed = false // 卸载后置位：异步 load() 恢复时不再触碰已销毁的实例

// ===== 语音台词（BWiki 语音:页面同步产物）=====
let voiceLines = null // {greet:[{action,text,audio}], boring:[], level:[], touch:[]}
let voiceAudio = null // 单实例：新台词打断上一条
let lineEndTimer = null
// 互动序列（点击推进）：[Boring, Click_*, Level]，台词优先按 action 映射，其次按分类轮换
let interactSeq = [] // [{label, category, preferred}]
let interactPtr = 0
let entryPlaying = false
let entrySequenceSeq = 0
const linePtr = { greet: 0, touch: 0 } // 各分类台词池轮换指针
let lastTapAt = 0

// 四星皮二阶段判据：语音库带非空 interact 池（互动1-5）。此时
// 入场 Greet 播互动1、Click_Interactivearea 进轮换、Exitidle 播退出互动。
// headInteract（head_click_interact=true，目前仅画中晨曦二阶段）：摸头台词是
// 一阶段风格、与二阶段语言风格不符，Click_Head 也进互动轮换。
function isStage2Interact() {
  return !!(voiceLines?.interact?.length)
}

// 二阶段语义下的待机判定：Exitidle 在二阶段是互动退出动作（播完要回 Idle），
// 不能按一阶段语义当成待机，否则 Loop=true 的它永远不会回待机。
function isStandbyMotion(label) {
  return classifyMotion(label, isStage2Interact()) === MOTION_STANDBY
}

function buildInteractSeq() {
  // 规则见 features/preview/motionRules.js：Boring / Level 各触发一次，
  // Click_Head 固定配摸头，其余 Click_* 按文件顺序依次对应普通触摸1..N。
  // preferred 命中时按 action 锁定台词，未命中按池内轮换。
  interactSeq = buildInteractSeqFromRules(
    motionList.map((m) => m.label),
    isStage2Interact(),
    !!(voiceLines?.head_click_interact),
  )
  interactPtr = 0
}

function standbyLabel() {
  // 只认模型声明的待机动作；零 Idle/Background 的模型宁可停住，
  // 也不能把排在前面的 Boring/Click 当待机播。
  return (motionList.find((m) => isStandbyMotion(m.label)) || {}).label || ''
}

function stopVoiceAudio() {
  if (voiceAudio) {
    try { voiceAudio.pause() } catch (e) { /* ignore */ }
    voiceAudio = null
  }
  if (lineEndTimer) { clearTimeout(lineEndTimer); lineEndTimer = null }
}

function hideSubtitleSoon(delay = 400) {
  if (lineEndTimer) clearTimeout(lineEndTimer)
  lineEndTimer = setTimeout(() => { subtitle.value = '' }, delay)
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

// 显示一条台词：语音音频由「语音互动」开关控制，台词框由「文本」开关控制，二者独立。
// preferredAction 指定优先播放的分类条目（如入场固定用「问候」/ 摸头固定取「摸头」）。
function playLine(category, preferredAction = '') {
  let pool = voiceLines?.[category]
  // Boring 动作读「闲置」台词；部分装束没有闲置条目时回落到「烦躁」，避免互动全哑火。
  if ((!pool || !pool.length) && category === 'idle') pool = voiceLines?.boring
  if (!pool || !pool.length) return
  let entry = findVoiceLine(pool, preferredAction)
  if (!entry) {
    // 摸头没有对应台词时不拿普通触摸顶替（宁可不说话，也不串台）。
    if (preferredAction === '摸头') return
    const usable = rotationPoolFor(category, pool)
    if (!usable.length) return
    const idx = (linePtr[category] || 0) % usable.length
    linePtr[category] = idx + 1
    entry = usable[idx]
  }
  stopVoiceAudio()
  const wantVoice = props.voice && entry.audio
  const wantText = props.subtitles && entry.text
  if (!wantVoice && !wantText) return
  let audioDone = !wantVoice // 无音频时视为“音频已结束”，台词只按文本时长显示
  const finishText = () => { if (audioDone) hideSubtitleSoon(500) }
  if (wantText) {
    subtitle.value = entry.text || ''
  }
  if (wantVoice) {
    try {
      const a = new Audio(assetUrl(entry.audio))
      a.volume = 0.95
      a.onended = () => { stopVoiceAudio(); audioDone = true; finishText() }
      voiceAudio = a
      a.play().catch(() => { stopVoiceAudio(); audioDone = true; finishText() })
    } catch (e) {
      audioDone = true
    }
  }
  if (!wantVoice) {
    // 无音频：按文本长度估算显示时长
    const text = entry.text || ''
    const dur = Math.min(7000, Math.max(2500, 1800 + text.length * 130))
    hideSubtitleSoon(dur)
  }
}

async function loadVoiceLines() {
  voiceLines = null
  const skin = props.skin
  if (!skin) return
  try {
    const res = await bridge.voiceLines(skin.character, skin.bundle, skin.name)
    if (disposed) return
    voiceLines = res && res.has ? res.lines : null
  } catch (e) {
    voiceLines = null
  }
}

// 入场序列：Greet（有则播）→ 问候台词（二阶段为互动1）→ Background 待机
async function startEntrySequence() {
  const greet = motionList.find((m) => classifyMotion(m.label) === MOTION_GREET)
  if (!greet) return
  const entrySeq = ++entrySequenceSeq
  entryPlaying = true
  const started = await playMotion(greet.label)
  if (!started || entrySeq !== entrySequenceSeq || !entryPlaying) {
    if (entrySeq === entrySequenceSeq) entryPlaying = false
    return
  }
  playLine(isStage2Interact() ? 'interact' : 'greet', isStage2Interact() ? '互动1' : '问候')
}

function activeMotionLabel() {
  const state = model?.internalModel?.motionManager?.state
  if (!state) return currentLabel
  const active = motionList.find(
    (item) => item.group === state.currentGroup && item.index === state.currentIndex,
  )
  return active?.label || currentLabel
}

async function interruptEntryToStandby() {
  entrySequenceSeq += 1
  entryPlaying = false
  stopVoiceAudio()
  subtitle.value = ''
  const standby = standbyLabel()
  // 用户主动点击中断 Greet 必须立即响应，不保留 Greet 末姿态做混合；
  // 平滑过渡只用于动作自然结束回 Idle。
  if (standby) await playMotion(standby)
  // 防止中断期间任何过期回调重新写入问候文本。
  stopVoiceAudio()
  subtitle.value = ''
}

// 点击互动：入场期间第一次点击只中断问候并回 Background；
// 待机后的下一次点击才顺序播放 Boring / Click_* / Level。
async function startInteractSequence() {
  const now = Date.now()
  if (now - lastTapAt < 600) return
  lastTapAt = now
  // 以 motionManager 当前 group/index 为真相源；本地布尔值可能被异步回调提前改写。
  if (classifyMotion(activeMotionLabel()) === MOTION_GREET) {
    await interruptEntryToStandby()
    return
  }
  if (!interactSeq.length) return
  const step = interactSeq[interactPtr % interactSeq.length]
  interactPtr += 1
  stopVoiceAudio()
  subtitle.value = ''
  const started = await playMotion(step.label)
  // 动作真实启动后才播放配套语音；加载失败/请求被新点击淘汰时不单独播语音。
  if (started) playLine(step.category, step.preferred)
}

async function load() {
  const base = assetUrl(props.skin.asset.dir)
  const modelPath = props.modelFile || props.skin.asset.model
  const meta = await (await fetch(`${base}/${modelPath}`)).json()
  // 装束切换/退出预览发生在加载期间：组件已卸载，不能再碰 refs 与 emit
  if (disposed) return

  motionList = []
  const motions = meta.FileReferences?.Motions || meta.Motions || {}
  const orderedGroups = Object.keys(motions).sort((a, b) => {
    if (a === 'All') return -1
    if (b === 'All') return 1
    return 0
  })
  const seenMotionFiles = new Set()
  for (const group of orderedGroups) {
    for (let index = 0; index < (motions[group] || []).length; index++) {
      const file = motions[group][index].File || ''
      const identity = file.replace(/\\/g, '/').toLocaleLowerCase()
      if (!file || seenMotionFiles.has(identity)) continue
      seenMotionFiles.add(identity)
      const label = file.split('/').pop().replace(/\.motion3\.json$/i, '')
      motionList.push({ label, file, group, index })
    }
  }
  emit('animations', motionList.map((m) => m.label))

  app = new Application({
    view: canvasRef.value,
    backgroundAlpha: 0,
    autoStart: true,
    resizeTo: wrapRef.value,
    antialias: true,
    // 透明 WebGL 画布在拖拽时容易在合成阶段留下上一帧的“虚影”，
    // preserveDrawingBuffer 可避免浏览器在合成后清空/残留缓冲
    preserveDrawingBuffer: true,
  })
  model = await Live2DModel.from(`${base}/${modelPath}`, {
    autoUpdate: true,
    ticker: app.ticker,
    motionPreload: 'ALL',
    // 关闭库自带的鼠标追踪/命中（autoHitTest 会监听 pointermove 并自动 focus），
    // 改为自定义 mousemove 实现，以便「鼠标追踪」开关能实时开/关。
    // （0.5.0 起 autoInteract 拆分为 autoHitTest + autoFocus）
    autoHitTest: false,
    autoFocus: false,
  })
  if (disposed) {
    // 卸载时 app 已 destroy，model 尚未挂到舞台：手动释放避免资源残留
    model.destroy?.()
    return
  }
  // 新版提取工具导出的 Texture2D 已由 AssetStudio/ImageSharp 做过标准正向转换，
  // 强制 textureFlipY = true 会把正常图片再次上下颠倒，导致网格 UV 错位成碎片。
  // model.internalModel.textureFlipY = true
  window.__L2D_MODEL__ = model
  applyPtnRenderMaterials(meta)
  cleanPtnModelParts()
  // 动作切换必须恢复真实 core 参数，而不是不存在的 internalModel.parameters。
  // 在任何动作播放前保存 PTN 清理后的参数/部件透明度基线。
  captureModelBaseline()
  afterMotionUpdateHandler = () => watchMotionFinished()
  model.internalModel?.on?.('afterMotionUpdate', afterMotionUpdateHandler)
  void initBackgroundPlayer(meta, `${base}/${modelPath}`)
  model.anchor.set(0.5, 0.5)
  app.stage.addChild(model)
  // pixi-live2d-display 会忽略动作自带的 Loop 标志（Meta.Loop 解析后从未应用），
  // 导致每个动作播完就触发“自动随机播放 Idle 姿势”，看起来像莫名换姿势。
  // 这里禁用库的自动随机 Idle：只有手动切换动画才会换姿势。
  const motionManager = model.internalModel?.motionManager
  if (motionManager) {
    motionManager.groups.idle = '__none__'
    motionManager.on('motionStart', (group, index) => {
      const m = motionManager.motionGroups?.[group]?.[index]
      // 游戏导出的 Greet/Boring/Click 等也常被错误标记为 Loop=true。
      // 只有 Background / Idle 是待机循环，其余入场与互动动作一律强制单次。
      const entry = motionList.find((item) => item.group === group && item.index === index)
      const autoLoop = !!entry && isStandbyMotion(entry.label)
      if (m && typeof m.setIsLoop === 'function') {
        m.setIsLoop(autoLoop)
        // 循环时禁止“循环重淡入”：Cubism SDK 在循环点会重置 fadeInStartTime
        //（_isLoopFadeIn 默认 true），fadeIn 权重瞬间归零再花 2 秒淡入，
        // 每循环一次就“顿一下回默认姿势再淡入”——这是 idle 卡顿感的来源。
        if (typeof m.setIsLoopFadeIn === 'function') m.setIsLoopFadeIn(false)
        // 禁用动作淡入淡出（默认 idle 组 fadeIn 长达 2 秒）：权重恒 1，
        // 循环点衔接平滑，消除“渐停/淡入”的卡顿感
        if (typeof m.setFadeInTime === 'function') m.setFadeInTime(0)
        if (typeof m.setFadeOutTime === 'function') m.setFadeOutTime(0)
      }
    })
  }
  // 以实际可见 drawable 顶点包围盒作为缩放/居中基准。
  // 某些 Unity 导出的模型声明画布远小于真实几何范围，若直接使用 model.width/height
  // 会把局部碎片放大到整屏；普通模型也可安全走同一算法。
  const measure = () => {
    if (!model) return
    const content = measureDrawableContent()
    if (content) {
      initW = content.width
      initH = content.height
      contentOffsetX = content.centerX
      contentOffsetY = content.centerY
    } else {
      initW = model.width || 0
      initH = model.height || 0
      contentOffsetX = 0
      contentOffsetY = 0
    }
    if (initW > 0 && initH > 0) applyLayout()
  }
  measure()
  if (!initW || !initH) {
    app.ticker.addOnce(measure)
    // 兜底：再等一帧仍为 0 就按 ticker 常规节奏重试
    let tries = 0
    const retry = () => {
      if ((initW && initH) || !model || tries++ > 30) return
      measure()
      requestAnimationFrame(retry)
    }
    requestAnimationFrame(retry)
  }
  // 鼠标追踪：视线/头部跟随鼠标（pixi-live2d-display 的 model.focus 会按
  // 鼠标相对模型的方向驱动 ParamEyeBall/Angle/BodyAngle，与库内置行为一致）
  window.addEventListener('mousemove', onTrackMove)
  // 台词数据拉取（BWiki 语音同步产物）；入场序列需要台词先就位，
  // 且互动序列要依赖台词池反查分类，所以必须先拉到台词再构建。
  await loadVoiceLines()
  if (disposed) return
  // 构建点击互动序列（除 Idle/Background/Greet 外，按提取顺序）
  buildInteractSeq()
  // 进场：父级手动选中的动画优先；否则按开关播入场序列（Greet→问候台词→Background），
  // 入场关闭或无 Greet 时直接进 Background 待机。
  // 父级收到 animations 列表后不再自动选中（避免覆盖入场），这里总是立即 emit。
  const motionLabels = motionList.map((m) => m.label)
  const want = props.animation || ''
  const standby = standbyLabel()
  const doEntry = props.introOn && motionList.some((m) => classifyMotion(m.label) === MOTION_GREET)
  emit('animations', motionLabels)
  if (want && motionList.some((m) => m.label === want)) {
    currentLabel = want
    playMotion(want)
  } else if (doEntry) {
    startEntrySequence()
  } else if (standby) {
    currentLabel = standby
    playMotion(standby)
  }
  emit('ready')
}

function captureModelBaseline() {
  const coreModel = model?.internalModel?.coreModel
  const raw = coreModel?.getModel?.()
  if (!raw) return
  baseParameterValues = raw.parameters?.values ? new Float32Array(raw.parameters.values) : null
  basePartOpacities = raw.parts?.opacities ? new Float32Array(raw.parts.opacities) : null
  // internalModel.update() 每帧会 loadParameters()，所以同步刷新 Cubism 的保存区。
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

function findBackgroundMotion(meta, modelUrl) {
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
async function initBackgroundPlayer(meta, modelUrl) {
  try {
    const url = findBackgroundMotion(meta, modelUrl)
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

function watchMotionFinished() {
  updateBackground(performance.now())
  if (!model) return
  const manager = model.internalModel?.motionManager
  // pixi-live2d-display 的 options.onFinish 在部分 Cubism4 路径不会透传；
  // 直接在 motion update 后观察队列完成，下一帧立即回 Idle，不等墙钟兜底。
  if (currentLabel && !isStandbyMotion(currentLabel)
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

function applyPtnRenderMaterials(meta) {
  const materialIds = meta?.PTNRender?.MaterialIds
  const im = model?.internalModel
  const drawables = im?.coreModel?.getModel?.()?.drawables
  if (!Array.isArray(materialIds) || !drawables?.constantFlags || materialIds.length !== drawables.count) return
  // Cubism flags: bit0=Additive, bit1=Multiplicative, bit2=DoubleSided,
  // bit3=InvertedMask。PTN 将混合/反遮罩语义移到了 Unity Material。
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
  let left = Infinity
  let right = -Infinity
  let top = Infinity
  let bottom = -Infinity
  for (let i = 0; i < drawables.count; i++) {
    if (!(drawables.dynamicFlags[i] & 1) || drawables.opacities[i] <= 0) continue
    const vertices = drawables.vertexPositions[i]
    if (!vertices) continue
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (let j = 0; j + 1 < vertices.length; j += 2) {
      const x = vertices[j]
      const y = vertices[j + 1]
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue
      minX = Math.min(minX, x)
      maxX = Math.max(maxX, x)
      minY = Math.min(minY, y)
      maxY = Math.max(maxY, y)
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

// 统一布局入口：初始布局与拖拽实时布局必须共用同一套数学。
// 关键是 contentOffset 补偿（可见内容包围盒中心相对模型声明画布中心的偏移）——
// 拖拽路径若漏掉它，按下后一动鼠标立绘就会瞬移 contentOffset × scale 的距离。
function setModelLayout(ox, oy) {
  const w = wrapRef.value?.clientWidth || 0
  const h = wrapRef.value?.clientHeight || 0
  if (!model || !initW || !initH || !w || !h) return
  const a = WL.anchor(props.alignment)
  const out = WL.l2dLayout(initW, initH, w, h, {
    scale: props.scale,
    offsetX: ox,
    offsetY: oy,
    alignX: a.x,
    alignY: a.y,
  })
  model.scale.set(out.sx)
  model.position.set(out.x - contentOffsetX * out.sx, out.y + contentOffsetY * out.sx)
}

function applyLayout() {
  setModelLayout(props.offsetX, props.offsetY)
}

function onResize() {
  applyLayout()
}

// 一次性动作播完回待机：按动作时长定时，而不是监听 motionFinish——
// stopAllMotions 强制停止旧动作时 motionFinish 也会触发，会把刚启动的
// 互动动作立刻切回 Background（竞态导致“只有语音没有动画”）。
let standbyTimer = null
let standbySwitchPending = false
let motionRequestSeq = 0
function clearStandbyTimer() {
  if (standbyTimer) {
    clearTimeout(standbyTimer)
    standbyTimer = null
  }
  standbySwitchPending = false
}

function queueStandbyTransition(requestSeq) {
  if (standbySwitchPending || disposed || requestSeq !== motionRequestSeq) return
  standbySwitchPending = true
  setTimeout(() => {
    if (disposed || requestSeq !== motionRequestSeq) {
      standbySwitchPending = false
      return
    }
    const standby = standbyLabel()
    if (standby) void playMotion(standby, { preservePose: true })
    else standbySwitchPending = false
  }, 0)
}
function motionDurationSeconds(target) {
  try {
    const mm = model.internalModel.motionManager
    const loaded = mm.motionGroups?.[target.group]?.[target.index]
    if (loaded && loaded._motionData && loaded._motionData.duration) {
      return loaded._motionData.duration
    }
  } catch (e) { /* 未加载时回退默认值 */ }
  return null
}
async function playMotion(label, { preservePose = false } = {}) {
  if (!model || !motionList.length) return false
  const target = motionList.find((m) => m.label === label)
  if (!target) return false
  const requestSeq = ++motionRequestSeq
  clearStandbyTimer()
  try {
    const im = model.internalModel
    const mm = im?.motionManager
    // 先确保目标动作完成加载，再停当前动作。否则入场期间点击时两个异步加载的
    // NORMAL 请求会交错抢占，旧 Greet 可能晚返回覆盖互动动作并卡住。
    const loaded = await mm?.loadMotion?.(target.group, target.index)
    if (disposed || requestSeq !== motionRequestSeq || !loaded) return false
    if (!preservePose) {
      // 主动切换动作：恢复模型加载后的完整基线（参数 + 部件透明度）。
      // Greet 等动作会把大量显隐参数置 0，而待机动作不一定覆盖它们，
      // 若不复位就只剩局部背景。
      // 自然结束回待机走 preservePose：官方动作末帧与 Idle 首帧逐参数一致，
      // 恢复基线反而会让待机动作不驱动的参数跳一下。
      restoreModelBaseline()
    }
    mm?.stopAllMotions?.()
    const isStandby = isStandbyMotion(label)
    if (isStandby) entryPlaying = false
    const returnToStandby = () => {
      if (disposed || requestSeq !== motionRequestSeq || isStandby) return
      queueStandbyTransition(requestSeq)
    }
    // FORCE(3) 保证点击能立刻打断正在播放的 Greet/上一条互动动作。
    // onFinish 属于当前动作实例：自然结束才触发，stopAllMotions 强制打断不会误触发。
    const started = await model.motion(target.group, target.index, 3, {
      onFinish: returnToStandby,
    })
    if (disposed || requestSeq !== motionRequestSeq || !started) return false
    cleanPtnModelParts()
    currentLabel = target.label
    if (!isStandby) {
      const dur = motionDurationSeconds(target) || 10
      // 极端运行时若不派发 onFinish，用 duration + 500ms 兜底回待机。
      standbyTimer = setTimeout(returnToStandby, dur * 1000 + 500)
    }
    return true
  } catch (e) {
    return false
  }
}

watch(
  () => [props.scale, props.offsetX, props.offsetY, props.alignment],
  () => applyLayout(),
)

watch(
  () => props.animation,
  (v) => {
    if (v) playMotion(v)
  },
)

// 鼠标追踪开关实时生效：关闭时视线/头部参数归零
watch(
  () => props.mouseTrack,
  (on) => setMouseTrack(on),
)

function onWheel(e) {
  e.preventDefault()
  const factor = Math.pow(1.1, -e.deltaY / 100)
  const next = WL.clampScale(props.scale * factor)
  emit('scaleChange', Math.round(next))
}

function onPanDown(e) {
  if (e.button !== 0) return
  drag = { x: e.clientX, y: e.clientY, ox: props.offsetX, oy: props.offsetY }
  wrapRef.value.style.cursor = 'grabbing'
}

// 鼠标追踪：鼠标移动时让角色视线/头部跟随（受「鼠标追踪」开关控制）
function onTrackMove(e) {
  if (!props.mouseTrack || !model) return
  model.focus(e.clientX, e.clientY)
}

// 切换鼠标追踪：关闭时把视线/头部参数平滑归零（focusController 目标置 0，
// 参数累加量归零后恢复动画原本的姿态）
function setMouseTrack(on) {
  if (on || !model) return
  try {
    model.internalModel?.focusController?.focus(0, 0, true)
  } catch (e) { /* 模型未就绪时忽略 */ }
}

// 拖拽/缩放事件绑定（幂等：先全部解绑再绑定，避免重复监听）
function bindPanEvents() {
  const wrap = wrapRef.value
  wrap.removeEventListener('wheel', onWheel)
  wrap.removeEventListener('mousedown', onPanDown)
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', onUp)
  wrap.addEventListener('wheel', onWheel, { passive: false })
  wrap.addEventListener('mousedown', onPanDown)
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  wrap.style.cursor = 'grab'
}

function onMove(e) {
  if (!drag || !model || !wrapRef.value) return
  drag.lastX = e.clientX
  drag.lastY = e.clientY
  const w = wrapRef.value.clientWidth || 1
  const h = wrapRef.value.clientHeight || 1
  // 鼠标位移 → 偏移百分比（dx/画布宽×100）→ 布局偏移回像素恰好也是 dx，
  // 与初始布局共用 setModelLayout（含 contentOffset 补偿）后即为 1:1 完美跟随
  const ox = WL.clampOffset(drag.ox + ((e.clientX - drag.x) / w) * 100)
  const oy = WL.clampOffset(drag.oy + ((e.clientY - drag.y) / h) * 100)
  setModelLayout(ox, oy)
}

function onUp(e) {
  if (!drag) return
  const w = wrapRef.value.clientWidth || 1
  const h = wrapRef.value.clientHeight || 1
  const lx = drag.lastX != null ? drag.lastX : drag.x
  const ly = drag.lastY != null ? drag.lastY : drag.y
  const movedDist = Math.hypot(lx - drag.x, ly - drag.y)
  const ox = Math.round(WL.clampOffset(drag.ox + ((lx - drag.x) / w) * 100))
  const oy = Math.round(WL.clampOffset(drag.oy + ((ly - drag.y) / h) * 100))
  drag = null
  wrapRef.value.style.cursor = 'grab'
  if (movedDist < 5) {
    // 点击（非拖拽）：顺序播放互动动作并配对应台词
    if (props.interact && model && motionList.length) startInteractSequence()
  } else {
    emit('panChange', { x: ox, y: oy })
  }
}

onMounted(() => {
  bindPanEvents()
  load().catch((e) => emit('error', e.message || String(e)))
  window.addEventListener('resize', onResize)
})

// keep-alive 缓存（图鉴页）下切走时暂停渲染：隐藏页继续跑 ticker 是纯资源浪费
onDeactivated(() => {
  if (app) app.ticker.stop()
  stopVoiceAudio()
})
onActivated(() => {
  if (app) app.ticker.start()
})

onBeforeUnmount(() => {
  disposed = true
  motionRequestSeq += 1
  bgPlayer = null
  bgParamIndex = null
  if (afterMotionUpdateHandler) {
    model?.internalModel?.off?.('afterMotionUpdate', afterMotionUpdateHandler)
    afterMotionUpdateHandler = null
  }
  drag = null
  stopVoiceAudio()
  clearStandbyTimer()
  window.removeEventListener('resize', onResize)
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mousemove', onTrackMove)
  window.removeEventListener('mouseup', onUp)
  if (app) app.destroy(true)
})

defineExpose({
  capture: () => {
    try {
      return canvasRef.value ? canvasRef.value.toDataURL('image/png') : null
    } catch (e) {
      return null
    }
  },
  // 导出壁纸封面：把整张立绘（按实际可渲染内容包围盒）完整装进 16:9 画布。
  // 不能直接截当前画面——用户可能放大/偏移导致裁切，且导出封面不应跟随拖拽。
  // 这里临时把渲染器尺寸切到封面尺寸、按内容居中缩放渲染一帧后立刻还原，
  // 整个流程同步执行，浏览器只会在结束后合成一次，预览不会闪。
  captureCover: () => {
    if (!app || !model || !canvasRef.value) return null
    // 取当前帧的实际可见内容包围盒：有些部件只在待机/动作里显示，
    // 用加载时的基线包围盒可能把当前帧多出来的部分裁掉。
    const content = measureDrawableContent()
    const contentW = content?.width || initW
    const contentH = content?.height || initH
    const centerX = content?.centerX ?? contentOffsetX
    const centerY = content?.centerY ?? contentOffsetY
    if (!contentW || !contentH) return null
    const width = 1280
    const height = 720
    const renderer = app.renderer
    const saved = {
      width: renderer.width,
      height: renderer.height,
      scaleX: model.scale.x,
      scaleY: model.scale.y,
      x: model.position.x,
      y: model.position.y,
    }
    try {
      // 留 4% 边距：头/脚/裙摆不贴边，WE 缩略图裁切也不容易切到内容。
      const scale = Math.min(width / contentW, height / contentH) * 0.96
      renderer.resize(width, height)
      model.scale.set(scale)
      model.position.set(width / 2 - centerX * scale, height / 2 + centerY * scale)
      app.render()
      return canvasRef.value.toDataURL('image/png')
    } catch (e) {
      return null
    } finally {
      renderer.resize(saved.width, saved.height)
      model.scale.set(saved.scaleX, saved.scaleY)
      model.position.set(saved.x, saved.y)
      app.render()
    }
  },
})
</script>

<style scoped>
.l2d-wrap {
  /* 绝对定位铺满舞台：pixi resizeTo 会把 canvas 内联尺寸写回布局，
     若 wrap 参与常规流会被 canvas 撑高（>舞台高），底部台词框会被 overflow:hidden 裁掉 */
  position: absolute;
  inset: 0;
  touch-action: none;
}

.l2d-wrap canvas {
  width: 100%;
  height: 100%;
}

/* 台词框：预览框下方居中（参考 WE 壁纸 #voiceTxt：半透明黑底白字 + 文字阴影 + 渐隐） */
.l2d-subtitle-box {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  max-width: 78%;
  background: rgba(0, 0, 0, 0.4);
  border-radius: 10px;
  padding: 8px 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  text-shadow: 0 2px 6px rgba(0, 0, 0, 0.9);
  backdrop-filter: blur(6px);
  z-index: 5;
  pointer-events: none;
}


.subtitle-txt {
  font-size: 14px;
  line-height: 1.6;
  color: #fff;
  letter-spacing: 0.4px;
  text-align: center;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.bubble-fade-enter-active,
.bubble-fade-leave-active {
  transition: opacity 0.25s ease;
}

.bubble-fade-enter-from,
.bubble-fade-leave-to {
  opacity: 0;
}
</style>
