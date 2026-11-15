<template>
  <div class="preview-page">
    <n-spin :show="loading" style="width: 100%; height: 100%">
      <template v-if="!character && loadError">
        <div class="preview-load-error">
          <n-alert type="error" :show-icon="true">加载失败：{{ loadError }}</n-alert>
          <n-button style="margin-top: 12px" @click="router.push('/gallery')">返回图鉴</n-button>
        </div>
      </template>
      <template v-if="character && currentSkin">
        <div class="preview-topbar">
          <n-button quaternary circle @click="router.push('/gallery')">←</n-button>
          <div class="preview-heading">
            <strong>{{ character.name }}</strong>
            <span class="wc-pill" :class="currentSkin.type === 'live2d' ? 'gold' : 'green'">
              {{ currentSkin.type === 'live2d' ? 'Live2D' : '静态' }}
            </span>
          </div>
          <n-button type="primary" @click="drawer = true">导出壁纸</n-button>
        </div>

        <div class="preview-body">
          <div ref="stageWrapRef" class="stage-wrap">
          <div ref="stageRef" class="preview-stage" :class="`bg-${bgStyle}`" :style="autoBgStyle">
              <n-alert
                v-if="engineError"
                type="error"
                :show-icon="true"
                closable
                class="engine-error"
                @close="engineError = ''"
              >
                预览引擎加载失败：{{ engineError }}
              </n-alert>
              <Live2DPreview
                v-if="currentSkin.type === 'live2d' && currentSkin.asset"
                ref="previewEl"
                :key="currentSkin.key"
                :skin="currentSkin"
                :animation="animation"
                :scale="scale"
                :offset-x="offsetX"
                :offset-y="offsetY"
                :alignment="alignment"
                @animations="onAnimations"
                @error="onEngineError"
                @scale-change="scale = $event"
                @pan-change="offsetX = $event.x; offsetY = $event.y"
              />
              <StaticPreview
                v-else-if="currentSkin.type === 'static' && currentSkin.asset"
                :key="currentSkin.key"
                :skin="currentSkin"
                :scale="scale"
                :offset-x="offsetX"
                :offset-y="offsetY"
                @scale-change="scale = $event"
                @pan-change="offsetX = $event.x; offsetY = $event.y"
                @error="onEngineError"
              />
              <div v-else class="preview-placeholder">
                <div class="preview-halo"></div>
                <div class="preview-title">{{ character.name }} · {{ currentSkin.name }}</div>
                <div class="preview-sub">
                  {{ currentSkin.status === 'remote' ? '未下载（下载后可预览）' : '引擎占位' }}
                </div>
              </div>
              <span class="zoom-hint">缩放 {{ scale }}%</span>
            </div>
          </div>

          <aside class="control-panel">
            <div class="panel-title">装束</div>
            <div class="skin-strip">
              <div
                v-for="(skin, i) in character.skins"
                :key="skin.key"
                class="skin-chip"
                :class="{ active: i === currentIndex }"
                @click="currentIndex = i"
              >
                {{ skin.name }}
              </div>
            </div>

            <div class="panel-title">背景样式</div>
            <n-select v-model:value="bgStyle" :options="bgOptions" />
            <input
              v-if="bgStyle === 'solid'"
              v-model="solidColor"
              type="color"
              class="solid-picker"
              title="自定义纯色背景"
            />

            <div class="panel-title">动画</div>
            <n-select v-model:value="animation" :options="animOptions" :disabled="!animOptions.length" placeholder="无可用动画" />
            <div v-if="animOptions.length" class="anim-nav">
              <n-button size="small" quaternary circle @click="stepAnim(-1)">←</n-button>
              <span class="anim-nav-label">{{ animIndex + 1 }} / {{ animOptions.length }}</span>
              <n-button size="small" quaternary circle @click="stepAnim(1)">→</n-button>
            </div>

            <div class="panel-title">缩放</div>
            <n-slider v-model:value="scale" :min="20" :max="800" :step="5" />

            <n-button type="primary" block style="margin-top: 18px" @click="drawer = true">
              导出为壁纸
            </n-button>
          </aside>
        </div>

        <n-drawer v-model:show="drawer" :width="430" placement="right">
          <n-drawer-content title="导出壁纸" closable>
            <div class="export-form">
              <label>壁纸类型</label>
              <n-select value="live2d" :options="[{ label: 'Live2D 壁纸', value: 'live2d' }]" disabled />

              <label>背景样式</label>
              <n-select v-model:value="bgStyle" :options="bgOptions" />
              <input
                v-if="bgStyle === 'solid'"
                v-model="solidColor"
                type="color"
                class="solid-picker"
                title="自定义纯色背景"
              />

              <label>缩放</label>
              <n-slider v-model:value="scale" :min="20" :max="800" :step="5" />
              <span class="bar-dim">缩放 {{ scale }}%（100 = 自适应）</span>

              <label>水平偏移（占画布宽 %）</label>
              <n-slider v-model:value="offsetX" :min="-100" :max="100" :step="1" />

              <label>垂直偏移（占画布高 %）</label>
              <n-slider v-model:value="offsetY" :min="-100" :max="100" :step="1" />

              <label>对齐方式</label>
              <n-select v-model:value="alignment" :options="[
                { label: '居中', value: 'center' },
                { label: '左上', value: 'left-top' },
                { label: '右上', value: 'right-top' },
                { label: '左下', value: 'left-bottom' },
                { label: '右下', value: 'right-bottom' },
              ]" />

              <n-button type="primary" block :loading="exporting" @click="doExport">
                {{ exporting ? '导出中…' : '导出' }}
              </n-button>

              <n-button type="success" block style="margin-top: 8px" :loading="applying" @click="doApply">
                {{ applying ? '正在导出并应用…' : '导出并应用' }}
              </n-button>

              <template v-if="applyDone">
                <n-alert v-if="applyResult && applyResult.ok" type="success" :show-icon="true">
                  已应用：{{ applyResult.project }}
                  <br />
                  壁纸已直接应用到桌面（以当前预览的缩放/偏移为准）
                </n-alert>
                <n-alert v-else type="error" :show-icon="true">
                  应用失败：{{ (applyResult && applyResult.error) || '未知错误' }}
                </n-alert>
              </template>

              <template v-if="exportDone">
                <n-alert v-if="exportResult && exportResult.ok" type="success" :show-icon="true">
                  导出完成：{{ exportResult.project }}
                  <br />
                  已打开文件夹，请把 index.html 拖入 Wallpaper Engine
                </n-alert>
                <n-alert v-else type="error" :show-icon="true">
                  导出失败：{{ (exportResult && exportResult.error) || '未知错误' }}
                </n-alert>
              </template>
            </div>
          </n-drawer-content>
        </n-drawer>
      </template>
    </n-spin>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NSpin, NSelect, NSlider,
  NDrawer, NDrawerContent, NAlert,
} from 'naive-ui'
import { bridge } from '../../bridge'
import Live2DPreview from '../../components/Live2DPreview.vue'
import StaticPreview from '../../components/StaticPreview.vue'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const loadError = ref('')
const engineError = ref('')
const character = ref(null)
const currentIndex = ref(0)
const drawer = ref(false)
const exporting = ref(false)
const exportDone = ref(false)
const exportResult = ref(null)
const applying = ref(false)
const applyDone = ref(false)
const applyResult = ref(null)

const bgStyle = ref('auto')
const animation = ref('')
const animOptions = ref([])
const scale = ref(100)
const offsetX = ref(0)
const offsetY = ref(0)
const alignment = ref('center')
const stageWrapRef = ref(null)
const stageRef = ref(null)
const previewEl = ref(null)
let stageRO = null

const STAGE_RATIO = 16 / 9

function fitStage() {
  const wrap = stageWrapRef.value
  const stage = stageRef.value
  if (!wrap || !stage) return
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  if (!w || !h) return
  let sw = w
  let sh = sw / STAGE_RATIO
  if (sh > h) {
    sh = h
    sw = sh * STAGE_RATIO
  }
  stage.style.width = `${Math.floor(sw)}px`
  stage.style.height = `${Math.floor(sh)}px`
}

const bgOptions = [
  { label: '自动取色', value: 'auto' },
  { label: '纯色', value: 'solid' },
  { label: '莫奈渐变', value: 'monet' },
  { label: '毛玻璃', value: 'frost' },
  { label: '星空', value: 'star' },
]

const currentSkin = computed(() => character.value?.skins[currentIndex.value] || null)

const bgCss = ref('')
const bgColors = ref([])
const solidColor = ref('')
const autoBgStyle = computed(() =>
  bgCss.value ? { background: bgCss.value } : {},
)

let bgSeq = 0
async function refreshBg() {
  const skin = currentSkin.value
  if (!skin) return
  const seq = ++bgSeq
  const res = await bridge.getPalette(skin.character, skin.bundle, skin.name, {
    mode: bgStyle.value,
    bgColor: bgStyle.value === 'solid' ? solidColor.value || undefined : undefined,
  })
  if (seq !== bgSeq) return // 已有更新的取色请求：丢弃过期结果，避免旧装束/旧设置的颜色串到新装束上
  const raw = res && typeof res.css === 'string' ? res.css : ''
  // 后端返回的是完整声明（background: ...;），存进 CSS 变量时只保留值部分
  bgCss.value = raw.replace(/^background:\s*/, '').replace(/;\s*$/, '')
  bgColors.value = Array.isArray(res?.colors) ? res.colors : []
  // 纯色模式未手动选色时，把取色首色回填到取色器：
  // 原生 <input type="color"> 空值显示为黑色，回填后控件与舞台背景一致
  if (bgStyle.value === 'solid' && !solidColor.value) {
    const first = Array.isArray(res?.colors) ? res.colors[0] : ''
    if (/^#[0-9a-fA-F]{6}$/.test(first || '')) solidColor.value = first
  }
}

watch(currentSkin, () => refreshBg(), { immediate: true })
watch(bgStyle, () => refreshBg())
watch(solidColor, () => refreshBg())

function onAnimations(names) {
  animOptions.value = names.map((n) => ({ label: n, value: n }))
  // 不自动选中第一个动作：待机/入场序列由 Live2DPreview 自身负责，
  // 避免“自动选中 + watch 立即 playMotion”覆盖入场动画。
}

const animIndex = computed(() => {
  const i = animOptions.value.findIndex((o) => o.value === animation.value)
  return i >= 0 ? i : 0
})

function stepAnim(dir) {
  const n = animOptions.value.length
  if (!n) return
  const next = (animIndex.value + dir + n) % n
  animation.value = animOptions.value[next].value
}

function onKeydown(e) {
  const t = e.target
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
  if (t && t.closest && t.closest('.n-select')) return
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
  if (animOptions.value.length) {
    e.preventDefault()
    stepAnim(e.key === 'ArrowLeft' ? -1 : 1)
  }
}

function onEngineError(msg) {
  console.error('[preview]', msg)
  engineError.value = msg || '未知错误'
}

let loadSeq = 0
async function loadCharacter(id) {
  loading.value = true
  loadError.value = ''
  const seq = ++loadSeq
  try {
    character.value = await bridge.getCharacter(id)
    if (seq !== loadSeq) return // 快速切换角色时丢弃过期响应，避免旧数据覆盖新数据
    currentIndex.value = 0
    animOptions.value = []
  } catch (e) {
    if (seq !== loadSeq) return
    character.value = null
    loadError.value = e.message || '加载失败'
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

watch(
  () => route.params.charId,
  (id) => {
    if (id) loadCharacter(id)
  },
)

onMounted(() => {
  const wrap = stageWrapRef.value
  if (wrap) {
    fitStage()
    stageRO = new ResizeObserver(fitStage)
    stageRO.observe(wrap)
  }
  loadCharacter(route.params.charId)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (stageRO) stageRO.disconnect()
  window.removeEventListener('keydown', onKeydown)
})

// 导出封面：截取完整立绘（按内容包围盒装进 16:9），不是当前缩放/偏移后的视口。
// 组件未就绪时返回 undefined，后端会保留导出器生成的兜底封面。
function exportCover() {
  const skin = currentSkin.value
  if (!skin || skin.type !== 'live2d') return undefined
  const el = previewEl.value
  if (!el || typeof el.captureCover !== 'function') return undefined
  return el.captureCover() || undefined
}

function exportOptions() {
  return {
    bg: bgStyle.value,
    bgColor: bgStyle.value === 'solid' ? solidColor.value || undefined : undefined,
    scale: scale.value,
    offsetX: offsetX.value,
    offsetY: offsetY.value,
    alignment: alignment.value,
    animation: animation.value,
    // 完整立绘封面 + 与预览一致的取色（后端用同一调色板重绘封面背景）
    previewImage: exportCover(),
    previewColors: bgColors.value,
  }
}

async function doExport() {
  const skin = currentSkin.value
  if (!skin) return
  exporting.value = true
  exportDone.value = false
  exportResult.value = null
  try {
    exportResult.value = await bridge.exportWallpaper(skin.character, skin.bundle, skin.name, exportOptions())
  } catch (e) {
    exportResult.value = { ok: false, error: e.message || '导出失败' }
  } finally {
    exporting.value = false
    exportDone.value = true
  }
}

async function doApply() {
  const skin = currentSkin.value
  if (!skin) return
  applying.value = true
  applyDone.value = false
  applyResult.value = null
  try {
    applyResult.value = await bridge.applyWallpaper(skin.character, skin.bundle, skin.name, exportOptions())
  } catch (e) {
    applyResult.value = { ok: false, error: e.message || '导出并应用失败' }
  } finally {
    applying.value = false
    applyDone.value = true
  }
}

</script>

<style scoped>
.preview-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.engine-error {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
  max-width: 90%;
}

.preview-topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 22px;
  border-bottom: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.86);
  backdrop-filter: blur(6px);
}

.preview-heading {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
}

.preview-heading strong {
  font-family: var(--font-serif);
  font-weight: 300;
  letter-spacing: 2px;
  font-size: 19px;
}

.preview-heading .en {
  color: var(--muted);
  font-size: 13px;
}

.preview-body {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 280px;
  min-height: 0;
}

.stage-wrap {
  min-height: 0;
  overflow: hidden;
  display: grid;
  place-items: center;
}

.zoom-hint {
  position: absolute;
  right: 16px;
  bottom: 14px;
  color: rgba(255, 255, 255, 0.55);
  font-size: 12px;
  background: rgba(0, 0, 0, 0.35);
  padding: 3px 10px;
  border-radius: 999px;
}

.control-panel {
  border-left: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.9);
  padding: 20px 18px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.anim-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.anim-nav-label {
  color: var(--muted);
  font-size: 12px;
  min-width: 52px;
  text-align: center;
}

.panel-title {
  color: var(--muted);
  font-size: 12px;
  margin-top: 8px;
}

.skin-strip {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.export-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.export-form label {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}
</style>
