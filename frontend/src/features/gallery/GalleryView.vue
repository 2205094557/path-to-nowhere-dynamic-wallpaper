<template>
  <div class="main-view" :class="{ 'is-fullscreen': fullscreen }">
    <!-- 顶栏 -->
    <header class="main-topbar" v-if="!fullscreen">
      <div class="main-title">
        <h1>无期迷途 · 壁纸工坊</h1>
        <span v-if="skinMode">共 {{ filteredSkins.length }} 个装束</span>
        <span v-else>共 {{ filtered.length }} 位角色 · {{ totalSkins }} 个装束</span>
      </div>
    </header>

    <div class="main-body">
      <!-- 左侧角色库 -->
      <aside class="char-rail" v-if="!fullscreen">
        <n-input v-model:value="searchInput" placeholder="角色名 / 装束…" clearable size="small">
          <template #prefix>🔍</template>
        </n-input>
        <div class="rail-tools">
          <span class="rail-tool" :class="{ active: favOnly }" @click="favOnly = !favOnly">
            {{ favOnly ? '★ 只看收藏中' : '☆ 只看收藏' }}
          </span>
          <span class="rail-tool" :class="{ active: multiMode }" @click="toggleMulti">
            {{ multiMode ? '✓ 多选中' : '多选' }}
          </span>
        </div>
        <div class="filter-panel">
          <div class="filter-field">
            <label>职业</label>
            <n-select v-model:value="filters.profession" :options="professionOptions" size="small" clearable placeholder="全部" />
          </div>
          <div class="filter-field">
            <label>稀有度</label>
            <n-select v-model:value="filters.rarity" :options="rarityOptions" size="small" clearable placeholder="全部" />
          </div>
          <div class="filter-field">
            <label>星级</label>
            <n-select v-model:value="filters.grade" :options="gradeOptions" size="small" clearable placeholder="全部" />
          </div>
          <div class="filter-field">
            <label>动态能力</label>
            <n-select v-model:value="filters.type" :options="typeOptions" size="small" />
          </div>
          <div class="filter-field">
            <label>装束系列</label>
            <n-select v-model:value="filters.series" :options="seriesOptions" size="small" clearable filterable placeholder="全部" />
          </div>
          <button class="wc-btn clear-filter-btn" @click="clearFilters">清空筛选</button>
        </div>
        <div ref="railListRef" class="rail-list">
          <template v-if="skinMode">
            <div
              v-for="item in filteredSkins"
              :key="item.skin.key"
              class="rail-card skin-row"
              :class="{ active: currentSkin && currentSkin.key === item.skin.key }"
              @click="onSkinClick(item)"
            >
              <div class="rail-avatar" :style="avatarStyle(item.character)">
                <img v-if="avatarUrl(item.character)" :src="avatarUrl(item.character)" class="rail-avatar-img" @error="onAvatarError(item.character)" />
                <span v-else class="rail-char">{{ item.character.name[0] }}</span>
              </div>
              <div class="rail-info">
                <div class="rail-name">{{ item.character.name }}</div>
                <div class="rail-skin" :title="item.skin.name">{{ item.skin.name }}</div>
                <div class="rail-meta">
                  <span class="wc-pill" :class="item.skin.type === 'live2d' ? 'gold' : 'green'">
                    {{ item.skin.type === 'live2d' ? 'L2D' : '静态' }}
                  </span>
                </div>
              </div>
              <span v-if="multiMode" class="rail-check" :class="{ on: isSelected(item.skin.key) }">✓</span>
            </div>
          </template>
          <template v-else>
            <div
              v-for="character in filtered"
              :key="character.id"
              class="rail-card"
              :class="{ active: character.id === selectedId }"
              @click="onCardClick(character)"
            >
              <div class="rail-avatar" :style="avatarStyle(character)">
                <img v-if="avatarUrl(character)" :src="avatarUrl(character)" class="rail-avatar-img" @error="onAvatarError(character)" />
                <span v-else class="rail-char">{{ character.name[0] }}</span>
              </div>
              <div class="rail-info">
                <div class="rail-name">{{ character.name }}</div>
                <div class="rail-meta">
                  <span v-if="character.rarity" class="wc-pill" :class="rarityClass(character.rarity)">{{ character.rarity }}</span>
                  <span v-for="t in characterTypes(character)" :key="t" class="wc-pill gold">L2D</span>
                </div>
              </div>
              <span v-if="multiMode" class="rail-check" :class="{ on: isSelected(character.id) }">✓</span>
            </div>
          </template>
        </div>
      </aside>

      <!-- 中央预览区 -->
      <div class="preview-zone">
        <div ref="stageWrapRef" class="stage-wrap">
          <div ref="stageRef" class="preview-stage" :class="`bg-${bgStyle}`" :style="autoBgStyle">
            <Live2DPreview
              v-if="currentSkin && currentSkin.type === 'live2d' && currentSkin.asset"
              ref="previewEl"
              :key="currentSkin.key + '-' + upscaleVersion"
              :skin="currentSkin"
              :model-file="upscaleModelFile"
              :animation="animation"
              :scale="scale"
              :offset-x="offsetX"
              :offset-y="offsetY"
              :alignment="alignment"
              :mouse-track="mouseTrackOn"
              :interact="interactOn"
              :voice="voiceOn"
              :subtitles="subtitlesOn"
              :intro-on="introOn"
              @animations="onAnimations"
              @error="onEngineError"
              @scale-change="scale = $event"
              @pan-change="offsetX = $event.x; offsetY = $event.y"
            />
            <StaticPreview
              v-else-if="currentSkin && currentSkin.type === 'static' && currentSkin.asset"
              :key="currentSkin.key + '-' + upscaleVersion"
              :skin="currentSkin"
              :image-file="upscaleImageFile"
              :scale="scale"
              :offset-x="offsetX"
              :offset-y="offsetY"
              @scale-change="scale = $event"
              @pan-change="offsetX = $event.x; offsetY = $event.y"
              @error="onEngineError"
            />
              <div v-else class="preview-placeholder">
              <div class="preview-halo"></div>
              <div class="preview-title">{{ selectedCharacter ? selectedCharacter.name : '选择角色' }} · {{ currentSkin ? currentSkin.name : '' }}</div>
              <div class="preview-sub">
                {{ currentSkin && currentSkin.status === 'remote' ? '未下载（可在下方装束条下载）' : '点击左侧角色开始预览' }}
              </div>
            </div>
            <div v-if="selectedCharacter && currentSkin" class="stage-info">
              <span class="stage-info-name">{{ selectedCharacter.name }}</span>
              <span class="stage-info-skin">{{ currentSkin.name }}</span>
            </div>
            <div v-if="currentSkin && currentSkin.type === 'live2d'" class="l2d-panel-a" :class="{ collapsed: l2dPanelCollapsed }">
              <div class="l2d-panel-a-head">
                <span class="l2d-panel-a-title"><span class="l2d-panel-a-dot"></span>预览控制</span>
                <button class="l2d-panel-a-collapse" :title="l2dPanelCollapsed ? '展开控制面板' : '收起控制面板'" @click="l2dPanelCollapsed = !l2dPanelCollapsed">
                  {{ l2dPanelCollapsed ? '⚙' : '−' }}
                </button>
              </div>
              <div v-if="!l2dPanelCollapsed" class="l2d-panel-a-body">
                <div class="l2d-switch-row">
                  <span class="l2d-switch-ico" :class="{ on: mouseTrackOn }">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4l6 15 2.2-6.8L20 10 5 4z"/></svg>
                  </span>
                  <div class="l2d-switch-txt">鼠标追踪<small>视线跟随鼠标（导出同步）</small></div>
                  <span class="l2d-switch" :class="{ on: mouseTrackOn }" :title="mouseTrackOn ? '关闭鼠标追踪' : '开启鼠标追踪'" @click="mouseTrackOn = !mouseTrackOn"></span>
                </div>
                <div class="l2d-switch-row">
                  <span class="l2d-switch-ico" :class="{ on: interactOn }">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
                  </span>
                  <div class="l2d-switch-txt">动作互动<small>触摸头部/身躯动作反馈</small></div>
                  <span class="l2d-switch" :class="{ on: interactOn }" :title="interactOn ? '关闭互动' : '开启互动'" @click="interactOn = !interactOn"></span>
                </div>
                <div class="l2d-switch-row">
                  <span class="l2d-switch-ico" :class="{ on: voiceOn }">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
                  </span>
                  <div class="l2d-switch-txt">语音互动<small>点击播放对应语音音频</small></div>
                  <span class="l2d-switch" :class="{ on: voiceOn }" :title="voiceOn ? '关闭语音' : '开启语音'" @click="voiceOn = !voiceOn"></span>
                </div>
                <div class="l2d-switch-row">
                  <span class="l2d-switch-ico" :class="{ on: subtitlesOn }">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M14 15h3M7 11h10"/></svg>
                  </span>
                  <div class="l2d-switch-txt">台词文本<small>播放语音时下方显示台词</small></div>
                  <span class="l2d-switch" :class="{ on: subtitlesOn }" :title="subtitlesOn ? '隐藏台词' : '显示台词'" @click="subtitlesOn = !subtitlesOn"></span>
                </div>
                <div class="l2d-switch-row">
                  <span class="l2d-switch-ico" :class="{ on: introOn }">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/></svg>
                  </span>
                  <div class="l2d-switch-txt">入场动画<small>进入立绘播放问候动作与台词</small></div>
                  <span class="l2d-switch" :class="{ on: introOn }" :title="introOn ? '关闭入场动画' : '开启入场动画'" @click="introOn = !introOn"></span>
                </div>
              </div>
            </div>
            <span class="zoom-hint">缩放 {{ scale }}%</span>
            <button class="stage-fullscreen-btn" @click="toggleFullscreen" :title="fullscreen ? '退出全屏' : '全屏预览'">
              {{ fullscreen ? '✕' : '⛶' }}
            </button>
          </div>
        </div>

        <!-- 装束选择栏 -->
        <div class="skin-bar">
          <div class="skin-strip">
            <button
              v-for="skin in (selectedCharacter ? selectedCharacter.skins : [])"
              :key="skin.key"
              type="button"
              class="skin-chip"
              :class="{ active: currentSkin && currentSkin.key === skin.key }"
              @click="selectSkin(skin)"
            >
              {{ skin.name || skin.displayLabel }}
            </button>
            <span v-if="selectedCharacter && !selectedCharacter.skins.length" class="bar-dim">无可用装束</span>
          </div>
          <div class="skin-status">
            <template v-if="selectedCharacter">
              <span v-if="currentSkin && currentSkin.status === 'downloaded'" class="dl-ok">✓ 当前已就绪</span>
              <button
                v-if="currentSkin && currentSkin.status === 'remote' && canDownload(currentSkin)"
                class="dl-btn"
                :disabled="downloading || batchDownloading || allDownloading"
                :title="`下载装束「${currentSkin.name}」资源`"
                @click="downloadCurrent"
              >
                {{ downloading ? '下载中…' : '⬇ 下载该装束' }}
              </button>
              <button
                v-if="remoteCount"
                class="dl-btn dl-btn-batch"
                :disabled="downloading || batchDownloading || allDownloading"
                :title="`一键下载 ${selectedCharacter.name} 全部 ${remoteCount} 个未下载装束`"
                @click="downloadAll"
              >
                {{ allDownloading ? `下载中 ${allDone}/${allTotal}` : `⬇ 批量下载（${remoteCount}）` }}
              </button>
              <button
                v-if="currentSkin"
                class="skin-fav-btn"
                :class="{ on: isSkinFav(currentSkin.key) }"
                :title="isSkinFav(currentSkin.key) ? '取消收藏该装束' : '收藏该装束'"
                @click="toggleSkinFav(currentSkin.key)"
              >
                {{ isSkinFav(currentSkin.key) ? '★' : '☆' }}
              </button>
            </template>
          </div>
        </div>
      </div>

      <!-- 右侧控制面板 -->
      <aside class="control-panel" v-if="!fullscreen">
        <div class="panel-card">
          <div class="panel-title">外观</div>
          <div class="panel-field">
            <label>背景</label>
            <div class="panel-row">
              <n-select v-model:value="bgStyle" :options="bgOptions" size="small" style="flex: 1" />
              <input
                v-if="bgStyle === 'solid'"
                v-model="solidColor"
                type="color"
                class="solid-picker"
                title="自定义纯色背景"
              />
            </div>
          </div>
          <div class="panel-field">
            <label>动画</label>
            <n-select
              v-model:value="animation"
              :options="animOptions"
              size="small"
              :disabled="!animOptions.length"
              placeholder="无"
              style="width: 100%"
            />
            <div v-if="animOptions.length" class="anim-nav">
              <button class="wc-btn anim-nav-btn" @click="stepAnim(-1)">←</button>
              <span class="anim-nav-label">{{ animIndex + 1 }} / {{ animOptions.length }}</span>
              <button class="wc-btn anim-nav-btn" @click="stepAnim(1)">→</button>
            </div>
          </div>
        </div>

        <div class="panel-card">
          <div class="panel-title">AI 超分</div>
          <template v-if="currentSkin && currentSkin.asset">
            <div class="panel-field">
              <label>预览版本 <span class="bar-dim">{{ upscaleInfo?.base_resolution || '' }}</span></label>
              <n-select v-model:value="upscaleVersion" :options="upscaleVersionOptions" size="small" style="width: 100%" />
            </div>
            <div class="panel-field">
              <label>引擎</label>
              <n-select v-model:value="upscaleAlgo" :options="upscaleAlgoOptions" size="small" style="width: 100%" />
            </div>
            <div class="panel-field">
              <label>放大倍率</label>
              <n-select v-model:value="upscaleScale" :options="upscaleScaleOptions" size="small" style="width: 100%" />
            </div>
            <div class="panel-field">
              <button class="wc-btn preset-import-btn" :class="{ active: showUpscaleAdv }" @click="showUpscaleAdv = !showUpscaleAdv">
                ⚙️ 高级参数
              </button>
            </div>
            <div v-if="showUpscaleAdv">
              <div class="panel-field">
                <label>降噪等级</label>
                <n-select v-model:value="upscaleDenoise" :options="upscaleDenoiseOptions" size="small" style="width: 100%" />
              </div>
              <div class="panel-field upscale-switch-row">
                <span>TTA 增强（更干净，更慢）</span>
                <n-switch v-model:value="upscaleTta" size="small" />
              </div>
              <div v-if="upscaleScale === 2" class="panel-field upscale-switch-row">
                <span>4x→2x 超采样（更锐利）</span>
                <n-switch v-model:value="upscaleDownsample" size="small" />
              </div>
            </div>
            <div class="export-actions">
              <button class="wc-btn gradient block" :disabled="upscaling" @click="doUpscale">
                {{ upscaling ? '正在深度计算…' : '⚡ 生成超分版本' }}
              </button>
              <button
                v-if="upscaleVersion !== 'none'"
                class="wc-btn danger-soft block"
                :disabled="upscaling"
                @click="onDeleteUpscaleVersion"
              >
                🗑 删除当前超分版本
              </button>
            </div>
            <p class="panel-tip">超分只生成独立副本，不改动官方原图；导出壁纸 / 图片会自动带上当前选中的版本。</p>
          </template>
          <p v-else class="panel-tip">请先下载该装束，再使用 AI 超分</p>
        </div>

        <div class="panel-card">
          <div class="panel-title">导出</div>
          <template v-if="currentSkin">
            <div class="panel-field" v-if="currentSkin.type !== 'static'">
              <label>壁纸类型</label>
              <n-select
                value="live2d"
                :options="[{ label: 'Live2D 壁纸', value: 'live2d' }]"
                size="small"
                disabled
              />
            </div>
            <div class="panel-field">
              <label>水平偏移（占画布宽 %）</label>
              <div class="offset-row">
                <n-slider v-model:value="offsetX" :min="-100" :max="100" :step="1" />
                <span class="bar-value" :class="{ zero: offsetX === 0 }">{{ offsetX > 0 ? '+' : '' }}{{ offsetX }}</span>
              </div>
            </div>
            <div class="panel-field">
              <label>垂直偏移（占画布高 %）</label>
              <div class="offset-row">
                <n-slider v-model:value="offsetY" :min="-100" :max="100" :step="1" />
                <span class="bar-value" :class="{ zero: offsetY === 0 }">{{ offsetY > 0 ? '+' : '' }}{{ offsetY }}</span>
              </div>
            </div>
            <div class="panel-field">
              <label>缩放 <span class="bar-dim">{{ scale }}%</span></label>
              <n-slider v-model:value="scale" :min="20" :max="800" :step="5" />
            </div>
            <div class="panel-field">
              <label>对齐方式</label>
              <n-select
                v-model:value="alignment"
                :options="[
                  { label: '居中', value: 'center' },
                  { label: '左上', value: 'left-top' },
                  { label: '右上', value: 'right-top' },
                  { label: '左下', value: 'left-bottom' },
                  { label: '右下', value: 'right-bottom' },
                ]"
                size="small"
                style="width: 100%"
              />
            </div>
            <div class="panel-field">
              <button class="wc-btn preset-import-btn" @click="openPresetImport">📥 导入 WE 预设 JSON</button>
            </div>
            <n-modal v-model:show="presetImportOpen" preset="card" title="导入 Wallpaper Engine 预设" style="width: 520px; max-width: 92vw">
              <p class="dim" style="margin-bottom: 10px">
                在 Wallpaper Engine 属性面板点「分享预设」复制内容（JSON 或 Base64 均可），
                粘贴到下面，即可把 缩放 / 偏移 / 对齐 / 鼠标追踪 应用到当前预览。
              </p>
              <n-input
                v-model:value="presetImportText"
                type="textarea"
                :rows="8"
                placeholder='{"scalectrl": 244, "offsetx": 8, "offsety": 4, "alignment": "center", "mousetrack": true}'
                style="font-family: var(--font-mono, Consolas, monospace); font-size: 12px"
              />
              <div class="preset-modal-actions">
                <n-button size="small" @click="presetImportOpen = false">取消</n-button>
                <n-button size="small" type="primary" @click="applyWePreset">应用</n-button>
              </div>
            </n-modal>
            <div class="export-actions">
              <button
                v-if="currentSkin.asset"
                class="wc-btn gradient block"
                :disabled="!currentSkin.asset || imageExporting"
                @click="doExportImage"
              >
                {{ imageExporting ? '导出中…' : '🖼 导出图片' }}
              </button>
              <button
                v-if="currentSkin.type !== 'static'"
                class="wc-btn gradient block"
                :disabled="!currentSkin.asset || exporting"
                @click="doExport"
              >
                {{ exporting ? '导出中…' : '导出壁纸' }}
              </button>
              <button class="wc-btn gradient block" :disabled="!currentSkin.asset || applying" @click="doApply">
                {{ applying ? '正在导出并应用…' : '导出并应用' }}
              </button>
            </div>
            <p v-if="currentSkin && !currentSkin.asset" class="panel-tip">请先在下方装束条下载该装束，再导出壁纸</p>
            <template v-if="applyDone">
              <n-alert v-if="applyResult && applyResult.ok" type="success" :show-icon="true" class="export-alert">
                已应用：{{ applyResult.project }}
              </n-alert>
              <n-alert v-else type="error" :show-icon="true" class="export-alert">
                应用失败：{{ (applyResult && applyResult.error) || '未知错误' }}
              </n-alert>
            </template>
            <template v-if="exportDone">
              <n-alert v-if="exportResult && exportResult.ok" type="success" :show-icon="true" class="export-alert">
                导出完成：{{ exportResult.project }}
              </n-alert>
              <n-alert v-else type="error" :show-icon="true" class="export-alert">
                导出失败：{{ (exportResult && exportResult.error) || '未知错误' }}
              </n-alert>
            </template>
          </template>
        </div>

        <div class="panel-card">
          <div class="panel-title">其他功能</div>
          <div class="panel-nav">
            <button
              class="wc-btn panel-nav-btn"
              :class="{ active: route.path === '/gallery' }"
              @click="router.push('/gallery')"
            >
              🧭 角色图鉴
            </button>
            <button
              class="wc-btn panel-nav-btn"
              :class="{ active: route.path === '/settings' }"
              @click="router.push('/settings')"
            >
              ⚙ 设置
            </button>
          </div>
        </div>
      </aside>
    </div>

    <div v-if="multiMode" class="batch-bar">
      <span>已选 {{ selectedSet.size }} {{ skinMode ? '个' : '位' }}</span>
      <n-button size="small" type="primary" :disabled="!selectedSet.size" :loading="batchDownloading" @click="batchDownload">
        {{ batchDownloading ? `下载中 ${batchDone}/${batchTotal}` : '批量下载所选' }}
      </n-button>
      <button class="wc-btn gold batch-btn" @click="selectAllFiltered">全选</button>
      <button class="wc-btn gold batch-btn" @click="clearSelection">清空</button>
    </div>

    <div v-if="downloadMsg" class="download-msg">{{ downloadMsg }}</div>

    <div v-if="dlProgress.active" class="dl-progress-card">
      <div class="dl-progress-head">
        <span class="dl-progress-label">{{ dlProgress.label }}</span>
        <button class="wc-btn danger-soft dl-progress-cancel" :disabled="cancelling" @click="onCancelDownloads">
          {{ cancelling ? '正在取消…' : '取消' }}
        </button>
      </div>
      <div class="dl-progress-track">
        <div class="dl-progress-fill" :style="{ width: dlPct + '%' }"></div>
      </div>
      <div class="dl-progress-meta">
        {{ dlProgress.done + dlProgress.failed }}/{{ dlProgress.total }}
        <span v-if="dlProgress.failed">（{{ dlProgress.failed }} 失败）</span>
        <span v-if="cancelling">正在停止…</span>
      </div>
      <div v-if="dlStage" class="dl-progress-stage">{{ dlStage }}</div>
    </div>

  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NInput, NModal, NSelect, NSlider, NAlert, NSwitch } from 'naive-ui'
import { assetUrl, bridge, metadataUrl, sseUrl } from '../../bridge'
import { playChime, warmAudio } from '../../utils/sound'
import Live2DPreview from '../../components/Live2DPreview.vue'
import StaticPreview from '../../components/StaticPreview.vue'

const route = useRoute()
const router = useRouter()

const characters = ref([])
const loading = ref(true)
const selectedId = ref(null)
const railListRef = ref(null)
const currentSkinKey = ref(null)
const fullscreen = ref(false)
const exporting = ref(false)
const exportDone = ref(false)
const exportResult = ref(null)
const applying = ref(false)
const applyDone = ref(false)
const applyResult = ref(null)
const downloading = ref(false)
const imageExporting = ref(false)
const allDownloading = ref(false)
const allDone = ref(0)
const allTotal = ref(0)
const allFailed = ref(0)
const downloadMsg = ref('')
const skinFavs = ref(new Set())
const favOnly = ref(false)
const avatars = ref({})
const avatarFailed = ref(new Set())
// avatars.json 的加载时间戳：拼到图片 URL 上，头像更新后重新加载图片（避免浏览器缓存旧图）
const avatarsStamp = ref(0)
const multiMode = ref(false)
const selectedSet = ref(new Set())
const batchDownloading = ref(false)
const batchDone = ref(0)
const batchTotal = ref(0)
const batchFailed = ref(0)
const batchCancelled = ref(0)
const downloadBatchId = ref('')
const cancelling = ref(false)
const dlProgress = reactive({ active: false, done: 0, total: 0, failed: 0, cancelled: 0, label: '' })
const dlStage = ref('')
let dlSource = null
const previewEl = ref(null)

const batchPct = computed(() => {
  if (!batchTotal.value) return 0
  const d = batchDone.value + batchFailed.value + batchCancelled.value
  return Math.min(100, Math.round((d / batchTotal.value) * 100))
})
const dlPct = computed(() => {
  if (!dlProgress.total) return 0
  const d = dlProgress.done + dlProgress.failed + dlProgress.cancelled
  return Math.min(100, Math.round((d / dlProgress.total) * 100))
})
const SKIN_FAV_KEY = 'ptn_wallpaper_skin_favs'
let msgTimer = null
let exportTimer = null
let applyTimer = null
const stageWrapRef = ref(null)
const stageRef = ref(null)
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

function showMsg(text) {
  downloadMsg.value = text
  clearTimeout(msgTimer)
  msgTimer = setTimeout(() => {
    downloadMsg.value = ''
  }, 5000)
}

const filters = reactive({ search: '', type: 'all', profession: null, rarity: null, grade: null, series: null })
const bgStyle = ref('auto')
const animation = ref('')
const animOptions = ref([])
const scale = ref(100)
const offsetX = ref(0)
const offsetY = ref(0)
const alignment = ref('center')
// L2D 鼠标追踪：角色视线/头部跟随鼠标。预览实时生效，导出壁纸联动
//（WE 面板「鼠标追踪」开关默认值跟随此选项）。
const mouseTrackOn = ref(true)
// WE 预设导入：粘贴 Wallpaper Engine「分享 JSON」的预设，应用到预览布局与开关
const presetImportOpen = ref(false)
const presetImportText = ref('')
const ALIGN_VALUES = ['center', 'left-top', 'right-top', 'left-bottom', 'right-bottom']

function openPresetImport() {
  presetImportText.value = ''
  presetImportOpen.value = true
}

// 解析 WE 分享预设 JSON 并应用到预览（scalectrl/offsetx/offsety/alignment/mousetrack）。
// 兼容两种格式：明文 JSON，或 WE「分享预设」的 Base64 编码（UTF-8）。
function applyWePreset() {
  let data
  const raw = (presetImportText.value || '').trim()
  try {
    data = JSON.parse(raw)
  } catch (e) {
    // 明文 JSON 解析失败 → 尝试 Base64 解码（WE 分享预设可切换 Base64 格式）
    try {
      const bin = atob(raw.replace(/\s+/g, ''))
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
      data = JSON.parse(new TextDecoder().decode(bytes))
    } catch (e2) {
      showMsg('无法解析：既不是 JSON，也不是 Base64 编码的 JSON')
      return
    }
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    showMsg('JSON 必须是对象格式')
    return
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
  const num = (v) => {
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : null
  }
  let applied = 0
  const sc = num(data.scalectrl)
  if (sc !== null) { scale.value = Math.round(clamp(sc, 20, 800)); applied++ }
  const ox = num(data.offsetx)
  if (ox !== null) { offsetX.value = Math.round(clamp(ox, -100, 100)); applied++ }
  const oy = num(data.offsety)
  if (oy !== null) { offsetY.value = Math.round(clamp(oy, -100, 100)); applied++ }
  // alignment 兼容 WE 的两种格式：数字下标（旧）或字符串对齐名（新）
  if (data.alignment !== undefined && data.alignment !== null) {
    const a = data.alignment
    const name = typeof a === 'number'
      ? (ALIGN_VALUES[a] || null)
      : (typeof a === 'string' && ALIGN_VALUES.includes(a) ? a : null)
    if (name) { alignment.value = name; applied++ }
  }
  if (typeof data.mousetrack === 'boolean') { mouseTrackOn.value = data.mousetrack; applied++ }
  if (applied) {
    showMsg(`已应用 ${applied} 项 WE 预设`)
    presetImportOpen.value = false
  } else {
    showMsg('未识别到可应用属性（scalectrl / offsetx / offsety / alignment / mousetrack）')
  }
}
const l2dPanelCollapsed = ref(false)
const interactOn = ref(true)
const voiceOn = ref(true)
const subtitlesOn = ref(true)
const introOn = ref(true)

// ===== AI 超分 =====
const upscaleInfo = ref(null) // { base_resolution, versions: [{id,label,model/image,resolution}] }
const upscaleVersion = ref('none') // 当前预览/导出选用的超分版本 id（none=官方原图）
const upscaleAlgo = ref('waifu2x')
const upscaleScale = ref(2)
const upscaleDenoise = ref(0)
const upscaleTta = ref(false)
const upscaleDownsample = ref(false)
const showUpscaleAdv = ref(false)
const upscaling = ref(false)

const upscaleAlgoOptions = [
  { label: 'Waifu2x（推荐，极佳兼容）', value: 'waifu2x' },
  { label: 'Real-CUGAN（二次元专属）', value: 'realcugan' },
  { label: 'Real-ESRGAN（精细渲染）', value: 'realesr' },
]
const upscaleScaleOptions = [
  { label: '2x 放大', value: 2 },
  { label: '4x 超清', value: 4 },
]
const upscaleDenoiseOptions = [
  { label: '无降噪（最锐利）', value: 0 },
  { label: '轻度降噪', value: 1 },
  { label: '中度降噪', value: 2 },
  { label: '强力去噪', value: 3 },
]

const upscaleVersionOptions = computed(() => {
  const base = upscaleInfo.value?.base_resolution || ''
  return [
    { label: base ? `原画质（官方 ${base}）` : '原画质（官方）', value: 'none' },
    ...(upscaleInfo.value?.versions || []).map((v) => ({ label: v.label, value: v.id })),
  ]
})
// 当前版本对应的衍生 model3.json / painting 文件名（传给预览组件热重载）
const upscaleModelFile = computed(() => {
  if (upscaleVersion.value === 'none') return ''
  const v = (upscaleInfo.value?.versions || []).find((x) => x.id === upscaleVersion.value)
  return currentSkin.value?.type === 'live2d' && v ? v.model || '' : ''
})
const upscaleImageFile = computed(() => {
  if (upscaleVersion.value === 'none') return ''
  const v = (upscaleInfo.value?.versions || []).find((x) => x.id === upscaleVersion.value)
  return currentSkin.value?.type === 'static' && v ? v.image || '' : ''
})

async function refreshUpscaleVersions() {
  const skin = currentSkin.value
  if (!skin || !skin.asset) {
    upscaleInfo.value = null
    upscaleVersion.value = 'none'
    return
  }
  try {
    const res = await bridge.getUpscaleVersions(skin.character, skin.bundle, skin.name)
    upscaleInfo.value = res && res.ok ? res : null
    // 当前选中版本已被删除时回落到原图
    if (upscaleVersion.value !== 'none' && !((res?.versions || []).some((v) => v.id === upscaleVersion.value))) {
      upscaleVersion.value = 'none'
    }
  } catch (e) {
    upscaleInfo.value = null
  }
}

async function doUpscale() {
  const skin = currentSkin.value
  if (!skin || !skin.asset || upscaling.value) return
  upscaling.value = true
  try {
    const res = await bridge.upscaleSkin(skin.character, skin.bundle, skin.name, {
      algorithm: upscaleAlgo.value,
      scale: upscaleScale.value,
      denoise: upscaleDenoise.value,
      tta: upscaleTta.value,
      downsample: upscaleDownsample.value && upscaleScale.value === 2,
    })
    if (res && res.ok) {
      showMsg(`✓ ${res.message || '超分完成'}`)
      const verId = res.version || 'none'
      // 必须先把新版本并入版本表，再切换 upscaleVersion：
      // 预览组件以 currentSkin.key + '-' + upscaleVersion 作为 key 重挂载，
      // 若此时版本表里还没有该版本，upscaleModelFile / upscaleImageFile 会算成
      // 空串，重挂载后仍加载原图（表现为下拉框显示超分版、画面还是原立绘，
      // 要手动再切一次版本才恢复）。直接用超分接口返回的 model/image 补条目，
      // 不依赖随后的磁盘扫描，扫描失败也不会退回原图。
      if (verId !== 'none') {
        const versions = (upscaleInfo.value?.versions || []).filter((v) => v.id !== verId)
        versions.push({
          id: verId,
          label: res.label || verId,
          model: res.model || '',
          image: res.image || '',
          resolution: res.resolution || '',
        })
        upscaleInfo.value = { ...(upscaleInfo.value || { ok: true }), versions }
      }
      upscaleVersion.value = verId
      await refreshUpscaleVersions()
    } else {
      showMsg(`✗ 超分失败：${(res && res.error) || '未知错误'}`)
    }
  } catch (e) {
    showMsg(`✗ 超分异常：${e.message}`)
  } finally {
    upscaling.value = false
  }
}

async function onDeleteUpscaleVersion() {
  const skin = currentSkin.value
  if (!skin || upscaleVersion.value === 'none') return
  const ver = upscaleVersion.value
  const res = await bridge.deleteUpscaleVersion(skin.character, skin.bundle, skin.name, ver)
  if (res && res.ok) {
    upscaleVersion.value = 'none'
    await refreshUpscaleVersions()
    showMsg('已删除该超分版本')
  } else {
    showMsg(`✗ 删除失败：${(res && res.error) || '未知错误'}`)
  }
}

const bgOptions = [
  { label: '自动取色', value: 'auto' },
  { label: '纯色', value: 'solid' },
  { label: '莫奈渐变', value: 'monet' },
  { label: '毛玻璃', value: 'frost' },
  { label: '星空', value: 'star' },
]

const typeOptions = [
  { label: '全部', value: 'all' },
  { label: 'L2D', value: 'live2d' },
  { label: '静态', value: 'static' },
]

// 职业（无期迷途图鉴维度）：坚韧 / 狂暴 / 诡秘 / 精准 / 异能 / 启迪
const PROFESSIONS = ['坚韧', '狂暴', '诡秘', '精准', '异能', '启迪']
const professionOptions = PROFESSIONS.map((v) => ({ label: v, value: v }))

const rarityOptions = computed(() =>
  [...new Set(characters.value.map((c) => c.rarity).filter(Boolean))].sort().map((v) => ({ label: v, value: v })),
)

const gradeOptions = computed(() =>
  [...new Set(characters.value.map((c) => c.grade).filter(Boolean))].sort().map((v) => ({ label: String(v), value: v })),
)

const seriesOptions = computed(() =>
  [...new Set(characters.value.flatMap((c) => c.skins.map((k) => k.series)).filter(Boolean))].sort().map((v) => ({ label: v, value: v })),
)

const totalSkins = computed(() => characters.value.reduce((n, c) => n + c.skins.length, 0))

const filtered = computed(() => {
  return characters.value.filter((c) => {
    const kw = filters.search.trim().toLowerCase()
    const skinHit = kw && c.skins.some((k) => (k.name || '').toLowerCase().includes(kw))
    if (kw && !c.name.toLowerCase().includes(kw) && !skinHit) return false
    const types = new Set(c.skins.map((k) => k.type))
    if (filters.type !== 'all' && !types.has(filters.type)) return false
    if (filters.profession && c.profession !== filters.profession) return false
    if (filters.rarity && c.rarity !== filters.rarity) return false
    if (filters.grade && c.grade !== filters.grade) return false
    if (filters.series && !c.skins.some((k) => k.series === filters.series)) return false
    return true
  })
})

// 按类型筛选时左侧改为装束级列表：直接展示具体的装束条目
// 只看收藏时同样切到装束级列表，展示的是收藏的单个装束
const skinMode = computed(() => filters.type !== 'all' || favOnly.value)

const filteredSkins = computed(() => {
  const out = []
  const kw = filters.search.trim().toLowerCase()
  for (const character of filtered.value) {
    for (const skin of character.skins) {
      if (filters.type !== 'all' && skin.type !== filters.type) continue
      if (favOnly.value && !skinFavs.value.has(skin.key)) continue
      if (filters.series && skin.series !== filters.series) continue
      if (
        kw &&
        !character.name.toLowerCase().includes(kw) &&
        !(skin.name || '').toLowerCase().includes(kw)
      ) {
        continue
      }
      out.push({ character, skin })
    }
  }
  return out
})

function clearFilters() {
  searchInput.value = ''
  filters.search = ''
  filters.type = 'all'
  filters.profession = null
  filters.rarity = null
  filters.grade = null
  filters.series = null
}

// 搜索框防抖：输入时不实时全量扫描数千条数据，停顿 250ms 后再过滤
const searchInput = ref('')
let searchTimer = null
watch(searchInput, (v) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    filters.search = v
    nextTick(() => {
      if (!skinMode.value && filtered.value.length === 1 && selectedId.value !== filtered.value[0].id) {
        selectCharacter(filtered.value[0])
      }
    })
  }, 250)
})

watch(() => filters.type, () => clearSelection())

const selectedCharacter = computed(() => characters.value.find((c) => c.id === selectedId.value) || null)
const currentSkin = computed(() => {
  const skins = selectedCharacter.value?.skins || []
  return skins.find((skin) => skin.key === currentSkinKey.value) || skins[0] || null
})
const resourceGroupDefinitions = [
  { key: 'base', label: '基础资源' },
  { key: 'extra', label: '补充资料' },
  { key: 'dynamic', label: '动态皮肤' },
]

const resourceGroups = computed(() => {
  const skins = selectedCharacter.value?.skins || []
  return resourceGroupDefinitions
    .map((group) => ({ ...group, items: skins.filter((skin) => skin.resourceGroup === group.key) }))
    .filter((group) => group.items.length)
})

function selectSkin(skin) {
  currentSkinKey.value = skin?.key || null
  animOptions.value = []
  animation.value = ''
}

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
// 切换装束：刷新超分版本列表，并把预览/导出版本回落到官方原图
watch(currentSkin, () => {
  upscaleVersion.value = 'none'
  refreshUpscaleVersions()
})
watch(currentSkin, () => {
  exportDone.value = false
  applyDone.value = false
  clearTimeout(exportTimer)
  clearTimeout(applyTimer)
})
watch(bgStyle, () => refreshBg())
watch(solidColor, () => refreshBg())

const WC = ['74,111,165', '180,120,140', '120,160,130', '201,168,76']

function hashOf(name) {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0
  return h
}

function avatarStyle(character) {
  const c = WC[hashOf(character.name) % 4]
  return { background: `linear-gradient(145deg, rgba(${c},0.42), rgba(240,235,227,0.7))` }
}

function avatarUrl(character) {
  const f = avatars.value[character.name]
  if (!f || avatarFailed.value.has(character.name)) return ''
  // 拼时间戳：头像映射更新后（检查更新/重启）URL 变化，浏览器会重新拉取图片
  const stamp = avatarsStamp.value ? `?v=${avatarsStamp.value}` : ''
  return assetUrl(`avatars/${f}.jpg`) + stamp
}

function onAvatarError(character) {
  const s = new Set(avatarFailed.value)
  s.add(character.name)
  avatarFailed.value = s
}

const RARITY_CLASSES = ['green', 'blue', 'gold', 'rose']

function rarityClass(r) {
  // 通用映射：按稀有度在当前数据里的排序稳定分配颜色，不绑定任何具体文案
  const idx = rarityOptions.value.findIndex((o) => o.value === r)
  return RARITY_CLASSES[(idx < 0 ? 0 : idx) % RARITY_CLASSES.length]
}

function characterTypes(character) {
  return [...new Set(character.skins.map((k) => k.type))].filter((t) => t === 'live2d')
}

function canDownload(skin) {
  return skin.type === 'live2d' || skin.type === 'static'
}

// 当前角色可下载（未下载且支持下载）的装束数：> 0 时显示批量下载按钮
const remoteCount = computed(() => {
  const c = selectedCharacter.value
  if (!c) return 0
  return c.skins.filter((k) => k.status === 'remote' && canDownload(k)).length
})

function getConcurrency() {
  try {
    const v = parseInt(localStorage.getItem('ptn_concurrency') || '4', 10)
    return Math.min(8, Math.max(1, v || 4))
  } catch (e) {
    return 4
  }
}

let downloadCancelled = false

function newDownloadId() {
  return `dl_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function startDl(label, total) {
  downloadCancelled = false
  cancelling.value = false
  downloadBatchId.value = newDownloadId()
  dlProgress.active = true
  dlProgress.done = 0
  dlProgress.failed = 0
  dlProgress.cancelled = 0
  dlProgress.total = total
  dlProgress.label = label
  dlStage.value = ''
  if (dlSource) {
    dlSource.close()
    dlSource = null
  }
  // SSE：实时显示 下载资源/提取/同步索引 等当前阶段（后端不支持时静默降级）
  try {
    dlSource = new EventSource(sseUrl())
    dlSource.addEventListener('stage', (e) => {
      try {
        const d = JSON.parse(e.data)
        dlStage.value = d.detail ? `${d.stage}：${d.detail}` : (d.stage || '')
      } catch (err) {
        /* 忽略异常事件 */
      }
    })
  } catch (err) {
    /* 后端未开 SSE 时静默 */
  }
}

function finishDl() {
  dlProgress.active = false
  downloadBatchId.value = ''
  if (dlSource) {
    dlSource.close()
    dlSource = null
  }
  dlStage.value = ''
}

async function onCancelDownloads() {
  if (downloadCancelled) return
  downloadCancelled = true
  cancelling.value = true
  if (downloadBatchId.value) {
    try {
      await bridge.cancelDownload(downloadBatchId.value)
    } catch (e) {
      /* 忽略取消请求失败，本地标志已生效 */
    }
  }
}

async function runDownloadPool(targets, limit, onProgress, batchId) {
  let done = 0
  const failedTargets = []
  let cancelledCount = 0
  let idx = 0
  const worker = async () => {
    while (idx < targets.length) {
      if (downloadCancelled) return
      const t = targets[idx++]
      try {
        const res = await bridge.downloadSkin(t.character, t.bundle, t.name, batchId)
        if (res && res.cancelled) {
          cancelledCount++
        } else if (res && res.ok) {
          done++
        } else {
          failedTargets.push(t)
        }
      } catch (e) {
        if (downloadCancelled) cancelledCount++
        else failedTargets.push(t)
      }
      if (onProgress) onProgress({ done, failed: failedTargets.length, cancelled: cancelledCount })
    }
  }
  const n = Math.min(Math.max(1, limit), targets.length)
  await Promise.all(Array.from({ length: n }, worker))
  return { done, failedTargets, cancelled: downloadCancelled || cancelledCount > 0, cancelledCount }
}

// 失败的装束自动重试一轮，避免网络抖动导致批量下载"莫名失败"
async function runPoolWithRetry(targets, limit, onProgress, batchId) {
  const first = await runDownloadPool(targets, limit, (p) => {
    if (onProgress) onProgress(p)
  }, batchId)
  if (first.cancelled || !first.failedTargets.length) {
    return {
      done: first.done,
      failed: first.failedTargets.length,
      cancelled: first.cancelled,
      cancelledCount: first.cancelledCount,
    }
  }
  const second = await runDownloadPool(first.failedTargets, limit, (p) => {
    if (onProgress) onProgress({ done: first.done + p.done, failed: p.failed, cancelled: first.cancelledCount + p.cancelled })
  }, batchId)
  return {
    done: first.done + second.done,
    failed: second.failedTargets.length,
    cancelled: second.cancelled,
    cancelledCount: first.cancelledCount + second.cancelledCount,
  }
}

function selectCharacter(character) {
  selectedId.value = character.id
  const first = character.skins.find((skin) => skin.status === 'downloaded') || character.skins[0] || null
  currentSkinKey.value = first?.key || null
  animOptions.value = []
  animation.value = ''
}

function loadFavs() {
  try {
    skinFavs.value = new Set(JSON.parse(localStorage.getItem(SKIN_FAV_KEY) || '[]'))
  } catch (e) {
    skinFavs.value = new Set()
  }
}

function saveFavs() {
  localStorage.setItem(SKIN_FAV_KEY, JSON.stringify([...skinFavs.value]))
}

function isSkinFav(key) {
  return skinFavs.value.has(key)
}

function toggleSkinFav(key) {
  const s = new Set(skinFavs.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  skinFavs.value = s
  saveFavs()
}

function toggleMulti() {
  multiMode.value = !multiMode.value
  if (!multiMode.value) clearSelection()
}

function isSelected(id) {
  return selectedSet.value.has(id)
}

function toggleSelect(id) {
  const s = new Set(selectedSet.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  selectedSet.value = s
}

function clearSelection() {
  selectedSet.value = new Set()
}

function selectAllFiltered() {
  if (skinMode.value) {
    for (const item of filteredSkins.value) selectedSet.value.add(item.skin.key)
  } else {
    for (const c of filtered.value) selectedSet.value.add(c.id)
  }
}

function onCardClick(character) {
  if (multiMode.value) toggleSelect(character.id)
  else selectCharacter(character)
}

function selectSkinItem(item) {
  const character = characters.value.find((c) => c.id === item.character.id)
  if (!character) return
  const skin = character.skins.find((candidate) => candidate.key === item.skin.key)
  if (!skin) return
  selectedId.value = character.id
  selectSkin(skin)
}

function onSkinClick(item) {
  if (multiMode.value) {
    toggleSelect(item.skin.key)
    return
  }
  selectSkinItem(item)
}

// 上下键翻动左侧列表选中项（角色/装束切换）：按当前筛选列表移动选中位置，
// 再滚动到可见。与缩放无关（切换角色不会缩放跳变）。
function railStep(dir) {
  const list = skinMode.value ? filteredSkins.value : filtered.value
  if (!list.length) return
  let cur = -1
  if (skinMode.value) {
    const key = currentSkin.value && currentSkin.value.key
    cur = list.findIndex((it) => it.skin.key === key)
  } else {
    cur = list.findIndex((c) => c.id === selectedId.value)
  }
  // 当前项不在筛选列表（搜索/筛选导致）时：next 从第一项开始，prev 从最后一项开始。
  const next = cur < 0 ? (dir > 0 ? 0 : list.length - 1) : (cur + dir + list.length) % list.length
  if (skinMode.value) selectSkinItem(list[next])
  else selectCharacter(list[next])
  // 键盘翻动后让左侧列表同步滚动到选中项（等 DOM 应用 .active 后再定位）
  nextTick(scrollRailToSelected)
}

// 让左侧角色库滚动到当前选中项（键盘翻动时保持选中项可见）。
// 用 getBoundingClientRect 差值计算，不受定位祖先影响；block=nearest 语义：
// 选中项在视口内就不动，只在越界时滚动到可见位置，连续翻动不会跳动。
function scrollRailToSelected() {
  const rail = railListRef.value
  if (!rail) return
  const active = rail.querySelector('.rail-card.active')
  if (!active) return
  const rTop = rail.getBoundingClientRect().top
  const rBottom = rail.getBoundingClientRect().bottom
  const eTop = active.getBoundingClientRect().top
  const eBottom = active.getBoundingClientRect().bottom
  if (eTop < rTop) {
    rail.scrollTop += eTop - rTop
  } else if (eBottom > rBottom) {
    rail.scrollTop += eBottom - rBottom
  }
}

async function batchDownload() {
  if (!selectedSet.value.size) return
  warmAudio()
  const targets = []
  if (skinMode.value) {
    for (const item of filteredSkins.value) {
      if (!selectedSet.value.has(item.skin.key)) continue
      const sk = item.skin
      if (sk.status === 'remote' && canDownload(sk)) {
        targets.push({ character: item.character.name, bundle: sk.bundle, name: sk.name })
      }
    }
  } else {
    for (const character of characters.value) {
      if (!selectedSet.value.has(character.id)) continue
      for (const sk of character.skins) {
        if (sk.status === 'remote' && canDownload(sk)) {
          targets.push({ character: character.name, bundle: sk.bundle, name: sk.name })
        }
      }
    }
  }
  if (!targets.length) {
    showMsg(skinMode.value ? '所选装束没有可下载的资源' : '所选角色没有可下载的资源')
    return
  }
  startDl(`批量下载（${targets.length} 个装束）`, targets.length)
  batchDownloading.value = true
  batchDone.value = 0
  batchFailed.value = 0
  batchCancelled.value = 0
  batchTotal.value = targets.length
  const res = await runPoolWithRetry(targets, getConcurrency(), ({ done, failed, cancelled }) => {
    batchDone.value = done
    batchFailed.value = failed
    batchCancelled.value = cancelled
    dlProgress.done = done
    dlProgress.failed = failed
    dlProgress.cancelled = cancelled
  }, downloadBatchId.value)
  batchDownloading.value = false
  finishDl()
  characters.value = await bridge.listCharacters()
  clearSelection()
  if (res.done > 0) playChime()
  if (res.cancelled) {
    showMsg(
      `已取消：完成 ${res.done} 个，失败 ${res.failed} 个，取消 ${res.cancelledCount || 0} 个，未下载 ${Math.max(
        0,
        batchTotal.value - res.done - res.failed - (res.cancelledCount || 0),
      )} 个`,
    )
  } else {
    showMsg(res.failed
      ? `批量下载完成：${res.done} 成功，${res.failed} 失败（已自动重试一轮）`
      : `✓ 批量下载完成（${batchTotal.value} 个装束）`)
  }
}

function onAnimations(names) {
  animOptions.value = names.map((n) => ({ label: n, value: n }))
  // 不再自动选中第一个动作：待机（Background）由 Live2DPreview 自身负责，
  // 入场动画（Greet→问候台词→Background）需要 animation 保持为空才不会被
  // “自动选中 + watch 立即 playMotion”覆盖。用户手动从下拉框选择时才播放。
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

function onEngineError(msg) {
  console.error('[preview]', msg)
  showMsg(`✗ 预览引擎加载失败：${msg}`)
}

async function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  try {
    if (fullscreen.value) {
      await document.documentElement.requestFullscreen?.()
    } else {
      await document.exitFullscreen?.()
    }
  } catch (e) {
    // 浏览器/WebView 不支持时仅用 CSS 全屏
  }
}

// 以浏览器实际全屏状态为准同步 UI（用户按 Esc / 系统手势退出全屏时，状态不脱节）
function onFullscreenChange() {
  fullscreen.value = !!document.fullscreenElement
}

function onKeydown(e) {
  if (e.key === 'Escape' && fullscreen.value) {
    fullscreen.value = false
    return
  }
  const t = e.target
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
  if (t && t.closest && t.closest('.n-select')) return
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    if (animOptions.value.length) {
      e.preventDefault()
      stepAnim(e.key === 'ArrowLeft' ? -1 : 1)
    }
    return
  }
  // 上下键：切换左侧列表选中项（角色/装束），保持选中项滚动可见。
  // 注意：不在此处改缩放——切换角色不会“缩放跳变”。
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
  if (fullscreen.value) return
  e.preventDefault()
  railStep(e.key === 'ArrowDown' ? 1 : -1)
}

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
    track: mouseTrackOn.value,
    interact: interactOn.value,
    voice: voiceOn.value,
    subtitle: subtitlesOn.value,
    intro: introOn.value,
    upscaleVersion: upscaleVersion.value,
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
    clearTimeout(exportTimer)
    exportTimer = setTimeout(() => {
      exportDone.value = false
    }, 8000)
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
    clearTimeout(applyTimer)
    applyTimer = setTimeout(() => {
      applyDone.value = false
    }, 8000)
  }
}

async function downloadCurrent() {
  const skin = currentSkin.value
  if (!skin) return
  warmAudio()
  startDl(`下载：${skin.name}`, 1)
  downloading.value = true
  downloadMsg.value = ''
  try {
    const res = await bridge.downloadSkin(skin.character, skin.bundle, skin.name, downloadBatchId.value)
    if (res && res.cancelled) {
      dlProgress.cancelled = 1
      showMsg('已取消下载')
    } else if (res && res.ok) {
      dlProgress.done = 1
      playChime()
      showMsg('✓ 下载完成，已自动提取入库')
      characters.value = await bridge.listCharacters()
    } else {
      dlProgress.failed = 1
      showMsg(`✗ 下载失败：${(res && res.error) || '未知错误'}`)
    }
  } catch (e) {
    showMsg(downloadCancelled ? '已取消下载' : `✗ 下载异常：${e.message}`)
  } finally {
    downloading.value = false
    finishDl()
  }
}

async function downloadAll() {
  const character = selectedCharacter.value
  if (!character) return
  warmAudio()
  const targets = character.skins.filter((sk) => sk.status === 'remote' && canDownload(sk))
  if (!targets.length) {
    showMsg('该角色没有可下载的资源')
    return
  }
  startDl(`下载全部：${character.name}`, targets.length)
  allDownloading.value = true
  allDone.value = 0
  allFailed.value = 0
  allTotal.value = targets.length
  const res = await runPoolWithRetry(targets, getConcurrency(), ({ done, failed, cancelled }) => {
    allDone.value = done
    allFailed.value = failed
    dlProgress.done = done
    dlProgress.failed = failed
    dlProgress.cancelled = cancelled
  }, downloadBatchId.value)
  allDownloading.value = false
  finishDl()
  characters.value = await bridge.listCharacters()
  if (res.done > 0) playChime()
  if (res.cancelled) {
    showMsg(`已取消：完成 ${res.done} 个，失败 ${res.failed} 个，取消 ${res.cancelledCount || 0} 个`)
  } else {
    showMsg(res.failed
      ? `下载全部完成：${res.done} 成功，${res.failed} 失败（已自动重试一轮）`
      : `✓ 已下载全部 ${allTotal.value} 个资源`)
  }
}

async function doExportImage() {
  const skin = currentSkin.value
  if (!skin) return
  imageExporting.value = true
  try {
    let res
    if (skin.type === 'static') {
      // 静态立绘：带上当前选中的超分版本（none=原图）
      res = await bridge.exportImage(skin.character, skin.bundle, skin.name, upscaleVersion.value)
    } else {
      // live2d：导出当前预览帧（所选动画的当前姿态），与预览所见一致
      const dataUrl =
        previewEl.value && typeof previewEl.value.capture === 'function'
          ? previewEl.value.capture()
          : null
      if (!dataUrl) {
        showMsg('✗ 暂无法读取当前预览画面')
        return
      }
      res = await bridge.exportImageData(skin.character, skin.bundle, skin.name, dataUrl, null)
    }
    if (res && res.ok) {
      showMsg(`✓ 图片已导出：${res.message}`)
    } else {
      showMsg(`✗ 导出失败：${(res && res.error) || '未知错误'}`)
    }
  } catch (e) {
    showMsg(`✗ 导出异常：${e.message}`)
  } finally {
    imageExporting.value = false
  }
}

onMounted(async () => {
  const wrap = stageWrapRef.value
  if (wrap) {
    fitStage()
    stageRO = new ResizeObserver(fitStage)
    stageRO.observe(wrap)
  }
  loadFavs()
  try {
    avatars.value = await (await fetch(metadataUrl('avatars.json'), { cache: 'no-store' })).json()
    avatarsStamp.value = Date.now()
    avatarFailed.value = new Set()
  } catch (e) {
    avatars.value = {}
  }
  try {
    characters.value = await bridge.listCharacters()
  } catch (e) {
    showMsg(`✗ 加载资源库失败：${e.message}`)
    characters.value = []
  }
  loading.value = false
  const first =
    characters.value.find((c) => c.skins.some((k) => k.status === 'downloaded')) || characters.value[0]
  if (first) selectCharacter(first)
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

// keep-alive 缓存后从设置等页面返回时触发：刷新数据但保留当前选中的角色/装束
onActivated(async () => {
  try {
    const fresh = await bridge.listCharacters()
    if (fresh && fresh.length) {
      characters.value = fresh
      if (!selectedCharacter.value) selectCharacter(fresh[0])
    }
  } catch (e) {
    /* 忽略刷新失败，保留现有数据 */
  }
  try {
    avatars.value = await (await fetch(metadataUrl('avatars.json'), { cache: 'no-store' })).json()
    // 头像映射刷新：更新版本戳（图片 URL 变化强制重载），并清失败集合让占位角色重试
    avatarsStamp.value = Date.now()
    avatarFailed.value = new Set()
  } catch (e) {
    /* 头像缺失时回退首字 */
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  clearTimeout(msgTimer)
  clearTimeout(exportTimer)
  clearTimeout(applyTimer)
  clearTimeout(searchTimer)
  if (stageRO) stageRO.disconnect()
})
</script>

<style scoped>
.main-view {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.main-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.86);
  backdrop-filter: blur(8px);
  flex-shrink: 0;
}

.main-title h1 {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 300;
  letter-spacing: 2px;
  font-size: 20px;
}

.main-title span {
  color: var(--muted);
  font-size: 12px;
  margin-left: 10px;
}

.main-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* ---- 左侧角色库 ---- */
.char-rail {
  width: 300px;
  flex-shrink: 0;
  border-right: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.8);
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
}

.rail-tools {
  display: flex;
  gap: 6px;
}

.filter-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.filter-field label {
  color: var(--muted);
  font-size: 12px;
}

.clear-filter-btn {
  grid-column: span 2;
  padding: 7px 0;
}

.rail-tool {
  flex: 1;
  text-align: center;
  padding: 6px 0;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.7);
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  font-weight: 300;
  transition: all 0.3s ease-out;
  user-select: none;
}

.rail-tool:hover {
  color: var(--blue);
  border-color: rgba(74, 111, 165, 0.5);
  background: #fff;
}

.rail-tool.active {
  background: linear-gradient(135deg, rgba(74, 111, 165, 0.16), rgba(133, 205, 202, 0.14));
  border-color: rgba(74, 111, 165, 0.55);
  color: #3a5a88;
  box-shadow: 0 4px 16px rgba(74, 111, 165, 0.14);
}

.rail-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}

.rail-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 16px;
  border: 1px solid transparent;
  background: rgba(250, 248, 245, 0.9);
  cursor: pointer;
  transition: all 0.3s ease-out;
}

.rail-card:hover {
  border-color: rgba(74, 111, 165, 0.3);
  transform: scale(1.015);
}

.rail-card.active {
  border-color: rgba(74, 111, 165, 0.5);
  background: linear-gradient(135deg, rgba(74, 111, 165, 0.16), rgba(180, 120, 140, 0.12));
  box-shadow: var(--shadow-soft);
}

.rail-avatar {
  width: 40px;
  height: 40px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  overflow: hidden;
}

.rail-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.rail-char {
  font-family: var(--font-serif);
  font-size: 16px;
  color: rgba(58, 58, 58, 0.8);
}

.rail-info {
  min-width: 0;
  flex: 1;
}

.rail-name {
  font-family: var(--font-serif);
  font-size: 14px;
  letter-spacing: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.rail-skin {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.rail-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.rail-check {
  position: absolute;
  bottom: 6px;
  right: 8px;
  z-index: 2;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.7);
  display: grid;
  place-items: center;
  font-size: 11px;
  color: transparent;
}

.rail-check.on {
  background: rgba(74, 111, 165, 0.85);
  color: #fff;
  border-color: transparent;
}

/* ---- 中心预览区 ---- */
.preview-zone {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.stage-wrap {
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
}

.preview-stage {
  position: relative;
  overflow: hidden;
  display: grid;
  place-items: center;
  border-radius: 14px;
  box-shadow: var(--shadow-hover);
}

.stage-info {
  position: absolute;
  top: 14px;
  left: 16px;
  z-index: 4;
  display: flex;
  align-items: baseline;
  gap: 8px;
  pointer-events: none;
  text-shadow: 0 1px 8px rgba(0, 0, 0, 0.45);
}

.stage-info-name {
  font-family: var(--font-serif);
  font-size: 20px;
  letter-spacing: 2px;
  color: rgba(250, 248, 245, 0.96);
}

.stage-info-skin {
  font-size: 13px;
  color: rgba(240, 235, 227, 0.75);
}

/* ---- L2D 预览控制面板 ---- */
.l2d-panel-a {
  position: absolute;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 6;
  width: 252px;
  background: linear-gradient(165deg, rgba(24, 34, 58, 0.78), rgba(10, 14, 24, 0.66));
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 18px;
  padding: 12px 12px 10px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(14px);
  font-family: var(--font-sans);
  transition: all 0.25s ease;
}

.l2d-panel-a.collapsed {
  width: 46px;
  padding: 10px 0;
}

.l2d-panel-a-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.l2d-panel-a-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-serif);
  font-size: 13px;
  letter-spacing: 3px;
  color: rgba(240, 235, 227, 0.92);
}

.l2d-panel-a-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--gold);
  box-shadow: 0 0 8px rgba(201, 168, 76, 0.8);
}

.l2d-panel-a-collapse {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.35);
  color: rgba(255, 255, 255, 0.7);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  transition: all 0.2s ease;
}

.l2d-panel-a-collapse:hover {
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
}

.l2d-panel-a.collapsed .l2d-panel-a-head {
  margin-bottom: 0;
  justify-content: center;
}

.l2d-panel-a.collapsed .l2d-panel-a-body {
  display: none;
}

/* 开关行 */
.l2d-switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 4px;
  border-radius: 12px;
  transition: background 0.2s ease;
}

.l2d-switch-row:hover {
  background: rgba(255, 255, 255, 0.05);
}

.l2d-switch-ico {
  width: 26px;
  height: 26px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.07);
  color: rgba(240, 235, 227, 0.85);
}

.l2d-switch-ico.on {
  color: var(--gold);
  background: rgba(201, 168, 76, 0.22);
}

.l2d-switch-txt {
  flex: 1;
  font-size: 12.5px;
  letter-spacing: 1px;
  color: rgba(240, 235, 227, 0.9);
}

.l2d-switch-txt small {
  display: block;
  font-size: 10.5px;
  color: rgba(240, 235, 227, 0.5);
  letter-spacing: 0.5px;
  margin-top: 1px;
}

.l2d-switch {
  position: relative;
  width: 38px;
  height: 22px;
  flex: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  cursor: pointer;
  transition: background 0.25s ease;
}

.l2d-switch::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
  transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}

.l2d-switch.on {
  background: linear-gradient(120deg, rgba(201, 168, 76, 0.9), rgba(180, 120, 140, 0.85));
}

.l2d-switch.on::after {
  transform: translateX(16px);
}

.stage-fullscreen-btn {
  position: absolute;
  top: 14px;
  right: 16px;
  z-index: 5;
  width: 34px;
  height: 34px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  background: rgba(10, 14, 24, 0.42);
  backdrop-filter: blur(6px);
  color: rgba(255, 255, 255, 0.9);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.3s ease-out;
}

.stage-fullscreen-btn:hover {
  background: rgba(10, 14, 24, 0.62);
  transform: scale(1.08);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
}

/* ---- 装束切换条 ---- */
.skin-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.9);
}

.skin-strip {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.skin-status {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.dl-ok {
  color: #6b9d7a;
  font-size: 13px;
}

/* ---- 右侧控制面板 ---- */
.control-panel {
  width: 300px;
  flex-shrink: 0;
  border-left: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.86);
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  overflow-y: auto;
}

.panel-card {
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px;
  box-shadow: var(--shadow-soft);
}

.panel-title {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 2px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px dashed rgba(120, 120, 120, 0.22);
}

.export-actions .wc-btn + .wc-btn {
  margin-top: 14px;
}

.dl-btn {
  padding: 6px 16px;
  font-size: 13px;
}

.export-alert {
  margin-top: 10px;
  font-size: 12px;
}

.panel-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}

.panel-field:last-child {
  margin-bottom: 0;
}

.panel-field label {
  color: var(--muted);
  font-size: 12px;
}

.panel-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.anim-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 8px;
}

.anim-nav-btn {
  padding: 4px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.4;
  min-width: 36px;
}

.anim-nav-label {
  color: var(--muted);
  font-size: 12px;
  min-width: 46px;
  text-align: center;
}

.panel-tip {
  color: #b07a3a;
  font-size: 12px;
  margin: 8px 0 0;
  line-height: 1.6;
}

.panel-nav {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed rgba(120, 120, 120, 0.22);
}

.panel-nav-btn {
  flex: 1;
  padding: 7px 10px;
  border-radius: 12px;
}

.panel-nav-btn.active {
  background: linear-gradient(135deg, rgba(74, 111, 165, 0.16), rgba(133, 205, 202, 0.14));
  border-color: rgba(74, 111, 165, 0.55);
  color: #3a5a88;
}

.bar-dim {
  color: var(--muted);
  font-size: 12px;
}

/* 偏移滑块行：滑块 + 实时数值并排，数值右对齐等宽数字，0 弱化 */
.offset-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.offset-row .n-slider {
  flex: 1;
  min-width: 0;
}
.bar-value {
  min-width: 42px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--blue);
}
.bar-value.zero {
  color: var(--muted);
  font-weight: 400;
}

/* WE 预设导入 */
.preset-import-btn {
  width: 100%;
  justify-content: center;
  font-size: 13px;
  padding: 7px 12px;
  border-style: dashed;
}
.preset-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}

/* AI 超分 */
.upscale-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.preset-import-btn.active {
  border-style: solid;
  color: var(--accent, #7aa2f7);
}

.download-msg {
  position: fixed;
  bottom: 60px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 20;
  padding: 10px 20px;
  border-radius: 999px;
  background: rgba(250, 248, 245, 0.95);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-soft);
  font-size: 13px;
  color: var(--ink);
}

.batch-bar {
  position: fixed;
  bottom: 70px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-radius: 999px;
  background: rgba(250, 248, 245, 0.96);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-hover);
  font-size: 13px;
  color: var(--ink);
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

.preview-placeholder {
  position: relative;
  text-align: center;
  user-select: none;
}

.preview-halo {
  width: 260px;
  height: 260px;
  margin: 0 auto 18px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(74, 111, 165, 0.35), rgba(180, 120, 140, 0.18) 45%, transparent 70%);
  filter: blur(2px);
  animation: halo-float 5s ease-in-out infinite;
}

@keyframes halo-float {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-16px) scale(1.06); }
}

.preview-title {
  font-family: var(--font-serif);
  font-weight: 300;
  font-size: 28px;
  letter-spacing: 5px;
  margin-bottom: 10px;
  color: rgba(250, 248, 245, 0.96);
  text-shadow: 0 2px 16px rgba(0, 0, 0, 0.4);
}

.preview-sub {
  color: rgba(240, 235, 227, 0.7);
  font-size: 13px;
}

/* 装束条下载按钮：与装束 chip 同款小药丸，金色描边款 / 浅金填充款区分主次 */
.skin-status .dl-btn {
  appearance: none;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid rgba(201, 168, 76, 0.5);
  background: rgba(255, 255, 255, 0.7);
  color: #9c7a34;
  font-family: var(--font-sans);
  font-weight: 300;
  font-size: 12px;
  letter-spacing: 0.5px;
  line-height: 1.5;
  white-space: nowrap;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.3s ease-out;
  user-select: none;
}

.skin-status .dl-btn:hover:not(:disabled) {
  background: rgba(201, 168, 76, 0.12);
  border-color: rgba(201, 168, 76, 0.75);
  color: #8a6a2c;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(201, 168, 76, 0.2);
}

.skin-status .dl-btn:active:not(:disabled) {
  transform: translateY(0) scale(0.97);
}

.skin-status .dl-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

.skin-status .dl-btn-batch {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.85), rgba(216, 186, 110, 0.85));
  border-color: transparent;
  color: #fff;
}

.skin-status .dl-btn-batch:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.95), rgba(216, 186, 110, 0.95));
  color: #fff;
  box-shadow: 0 4px 14px rgba(201, 168, 76, 0.3);
}

/* 皮肤收藏星标（装束切换条右侧，始终可点击） */
.skin-fav-btn {
  border: none;
  width: 32px;
  height: 32px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(74, 111, 165, 0.14), rgba(133, 205, 202, 0.18));
  border: 1px solid rgba(74, 111, 165, 0.35);
  color: rgba(74, 111, 165, 0.75);
  font-size: 15px;
  cursor: pointer;
  display: inline-grid;
  place-items: center;
  transition: transform 0.25s ease-out, color 0.25s ease-out, box-shadow 0.25s ease-out;
}
.skin-fav-btn:hover {
  transform: scale(1.12);
  box-shadow: 0 4px 14px rgba(74, 111, 165, 0.25);
}
.skin-fav-btn.on {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.9), rgba(240, 200, 90, 0.95));
  border-color: rgba(201, 168, 76, 0.65);
  color: #fff;
  box-shadow: 0 4px 16px rgba(201, 168, 76, 0.4);
}

.resource-catalog {
  align-items: stretch;
  gap: 12px;
}
.resource-groups {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.resource-group {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.resource-group-title {
  flex: 0 0 64px;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  text-align: right;
  white-space: nowrap;
}
.resource-group .skin-strip {
  min-width: 0;
  flex: 1;
  padding-bottom: 2px;
}
.resource-group .skin-chip {
  appearance: none;
  font: inherit;
  cursor: pointer;
}

/* 批量操作栏内的渐变按钮 */
.batch-bar .batch-btn {
  padding: 6px 16px;
  font-size: 12px;
}

/* 右下角下载进度条 */
.dl-progress-card {
  position: fixed;
  right: 0;
  bottom: 24px;
  z-index: 30;
  width: 300px;
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(250, 248, 245, 0.97);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-hover);
  backdrop-filter: blur(8px);
}
.dl-progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.dl-progress-label {
  font-size: 13px;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dl-progress-cancel {
  padding: 4px 14px;
  font-size: 12px;
  flex-shrink: 0;
}
.dl-progress-track {
  height: 8px;
  border-radius: 999px;
  background: rgba(74, 111, 165, 0.12);
  overflow: hidden;
}
.dl-progress-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #4a6fa5, #85cdca);
  transition: width 0.3s ease-out;
}
.dl-progress-meta {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}

.dl-progress-stage {
  margin-top: 6px;
  font-size: 12px;
  color: var(--blue);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 全屏模式 */
.main-view.is-fullscreen .preview-zone {
  width: 100%;
}
</style>
