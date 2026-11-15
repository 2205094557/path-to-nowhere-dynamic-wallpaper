/*
 * 壁纸布局统一模块（预览与导出共用，单一事实来源）。
 *
 * 语义约定：
 *  - scale：百分比，100 = 自适应（contain，模型占画布 90%），20~300。
 *  - offsetX / offsetY：画布宽/高的百分比，±100。
 *  - alignX / alignY：0=左/上，0.5=居中，1=右/下（画布坐标系，Y 向下）。
 *
 * 经典脚本（无 import/export），可被导出壁纸 <script> 直接加载；
 * 前端通过副作用 import 读取 window.WallpaperLayout。
 */
(function (root) {
  'use strict'

  var MARGIN = 0.98 // 模型包围盒最多占画布 98%（撑满预览框）
  var MIN_SCALE = 20
  var MAX_SCALE = 800
  var MAX_OFFSET = 100
  var ALIGN_ORDER = ['center', 'left-top', 'right-top', 'left-bottom', 'right-bottom']
  var ALIGNMENTS = {
    center: { x: 0.5, y: 0.5 },
    'left-top': { x: 0, y: 0 },
    'right-top': { x: 1, y: 0 },
    'left-bottom': { x: 0, y: 1 },
    'right-bottom': { x: 1, y: 1 },
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v))
  }

  function clampScale(pct) {
    return clamp(pct == null ? 100 : pct, MIN_SCALE, MAX_SCALE)
  }

  function clampOffset(pct) {
    return clamp(pct == null ? 0 : pct, -MAX_OFFSET, MAX_OFFSET)
  }

  function fitFactor(scalePct) {
    return clampScale(scalePct) / 100
  }

  function anchor(name) {
    var a = ALIGNMENTS[name || 'center']
    return a ? { x: a.x, y: a.y } : { x: 0.5, y: 0.5 }
  }

  /*
   * Live2D：把模型画布（moc 尺寸）放进画布，返回模型 scale/position（锚点 0.5,0.5）。
   */
  function l2dLayout(modelW, modelH, canvasW, canvasH, opts) {
    opts = opts || {}
    var scale = fitFactor(opts.scale)
    var ox = clampOffset(opts.offsetX)
    var oy = clampOffset(opts.offsetY)
    var ax = opts.alignX != null ? opts.alignX : 0.5
    var ay = opts.alignY != null ? opts.alignY : 0.5
    var fit = Math.min(canvasW / Math.max(1, modelW), canvasH / Math.max(1, modelH)) * MARGIN
    var sx = fit * scale
    return {
      sx: sx,
      x: (modelW * sx) / 2 + ax * (canvasW - modelW * sx) + ((ox / 100) * canvasW),
      y: (modelH * sx) / 2 + ay * (canvasH - modelH * sx) + ((oy / 100) * canvasH),
    }
  }

  /*
   * Wallpaper Engine 布局控制器：WE 属性与 localStorage 共用同一状态。
   * “调整位置”开启时只接管左键拖动，不监听滚轮；关闭后立即定格当前位置。
   * 如果稳定存储键已有记录，首次包含布局字段的 WE 推送不覆盖该记录；没有记录时
   * 首推按存在字段采纳（不要求 alignment 一定出现），从而恢复 WE 按路径保存的值。
   */
  function layoutController(el, opts) {
    opts = opts || {}
    var state = {
      scale: clampScale(opts.scale),
      offsetX: clampOffset(opts.offsetX),
      offsetY: clampOffset(opts.offsetY),
      alignment: ALIGNMENTS[opts.alignment] ? opts.alignment : 'center',
      adjustSize: !!opts.adjustSize,
    }
    var storageKey = opts.storageKey || ''
    var restored = false
    var firstManagedPush = true
    var down = null
    var saveTimer = null

    function snapshot() {
      return {
        version: 1,
        scale: state.scale,
        offsetX: state.offsetX,
        offsetY: state.offsetY,
        alignment: state.alignment,
      }
    }

    function load() {
      if (!storageKey || !root.localStorage) return
      try {
        var saved = JSON.parse(root.localStorage.getItem(storageKey) || 'null')
        if (!saved || saved.version !== 1) return
        if (!isFinite(Number(saved.scale)) || !isFinite(Number(saved.offsetX)) || !isFinite(Number(saved.offsetY))) return
        state.scale = clampScale(Number(saved.scale))
        state.offsetX = clampOffset(Number(saved.offsetX))
        state.offsetY = clampOffset(Number(saved.offsetY))
        if (ALIGNMENTS[saved.alignment]) state.alignment = saved.alignment
        restored = true
      } catch (e) { /* 损坏存储回退导出默认值 */ }
    }

    function save() {
      if (!storageKey || !root.localStorage) return
      if (saveTimer) { root.clearTimeout(saveTimer); saveTimer = null }
      try { root.localStorage.setItem(storageKey, JSON.stringify(snapshot())) } catch (e) {}
    }

    function scheduleSave() {
      if (saveTimer) root.clearTimeout(saveTimer)
      saveTimer = root.setTimeout(save, 180)
    }

    function changed(persist) {
      if (opts.onChange) opts.onChange(state)
      if (persist) scheduleSave()
    }

    function finishDrag() {
      if (!down) return
      var pointerId = down.pointerId
      down = null
      try {
        if (el.hasPointerCapture && el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId)
      } catch (e) {}
      save()
    }

    function setAdjustSize(on) {
      state.adjustSize = !!on
      if (!state.adjustSize) finishDrag()
      if (opts.onModeChange) opts.onModeChange(state.adjustSize)
    }

    function onPointerDown(e) {
      if (!state.adjustSize || (e.button != null && e.button !== 0)) return
      down = {
        pointerId: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        offsetX: state.offsetX,
        offsetY: state.offsetY,
      }
      try { el.setPointerCapture(e.pointerId) } catch (err) {}
      e.preventDefault()
    }

    function onPointerMove(e) {
      if (!state.adjustSize || !down || e.pointerId !== down.pointerId) return
      var rect = el.getBoundingClientRect()
      state.offsetX = clampOffset(down.offsetX + ((e.clientX - down.x) / Math.max(1, rect.width)) * 100)
      state.offsetY = clampOffset(down.offsetY + ((e.clientY - down.y) / Math.max(1, rect.height)) * 100)
      changed(true)
      e.preventDefault()
    }

    function onPointerUp(e) {
      if (!down || e.pointerId !== down.pointerId) return
      finishDrag()
      e.preventDefault()
    }

    function applyUserProperties(properties) {
      properties = properties || {}
      var hasLayout = !!(properties.scalectrl || properties.offsetx || properties.offsety || properties.alignment)
      var hasManaged = hasLayout || !!properties.adjustsize
      var skipLayout = firstManagedPush && restored
      if (hasLayout && !skipLayout) {
        if (properties.scalectrl) state.scale = clampScale(Number(properties.scalectrl.value))
        if (properties.offsetx) state.offsetX = clampOffset(Number(properties.offsetx.value))
        if (properties.offsety) state.offsetY = clampOffset(Number(properties.offsety.value))
        if (properties.alignment) {
          var av = properties.alignment.value
          if (typeof av === 'number') av = ALIGN_ORDER[av]
          if (ALIGNMENTS[av]) state.alignment = av
        }
        save()
      }
      if (properties.adjustsize) setAdjustSize(properties.adjustsize.value === true || properties.adjustsize.value === 1)
      if (hasManaged) firstManagedPush = false
      changed(false)
    }

    load()
    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('pointermove', onPointerMove)
    el.addEventListener('pointerup', onPointerUp)
    el.addEventListener('pointercancel', onPointerUp)
    el.addEventListener('lostpointercapture', finishDrag)
    root.addEventListener('pagehide', save)

    return {
      state: state,
      restored: function () { return restored },
      applyUserProperties: applyUserProperties,
      setAdjustSize: setAdjustSize,
      save: save,
      destroy: function () {
        finishDrag()
        if (saveTimer) root.clearTimeout(saveTimer)
        el.removeEventListener('pointerdown', onPointerDown)
        el.removeEventListener('pointermove', onPointerMove)
        el.removeEventListener('pointerup', onPointerUp)
        el.removeEventListener('pointercancel', onPointerUp)
        el.removeEventListener('lostpointercapture', finishDrag)
        root.removeEventListener('pagehide', save)
      },
    }
  }

  root.WallpaperLayout = {
    MARGIN: MARGIN,
    MIN_SCALE: MIN_SCALE,
    MAX_SCALE: MAX_SCALE,
    MAX_OFFSET: MAX_OFFSET,
    ALIGN_ORDER: ALIGN_ORDER,
    ALIGNMENTS: ALIGNMENTS,
    clampScale: clampScale,
    clampOffset: clampOffset,
    fitFactor: fitFactor,
    anchor: anchor,
    l2dLayout: l2dLayout,
    layoutController: layoutController,
  }
})(typeof window !== 'undefined' ? window : this)
