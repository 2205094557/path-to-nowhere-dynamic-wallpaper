// 前端与后端的统一通信层。
// 浏览器预览模式（无 pywebview）时数据读本地元数据、动作走本地后端 API。

const isPywebview = () => typeof window !== 'undefined' && !!window.pywebview

// 开发模式下预览素材走 Vite 的 /@fs/，需要磁盘绝对路径；路径集中在
// .env.development 的 VITE_DEV_RESOURCES 配置，换机器开发只需改 env 文件。
const DEV_RESOURCES = import.meta.env.VITE_DEV_RESOURCES || ''
// 打包版通过 .env.packaged 的 VITE_API_BASE 指向自己的后端端口，避免连到开发版后端
const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8766'

const API_ACTIONS = {
  downloadSkin: { path: '/api/download', method: 'POST' },
  cancelDownload: { path: '/api/download/cancel', method: 'POST' },
  deleteSkin: { path: '/api/library/delete', method: 'POST' },
  deleteSkinsBatch: { path: '/api/library/delete-batch', method: 'POST' },
  clearDownloads: { path: '/api/library/clear', method: 'POST' },
  cleanupBundles: { path: '/api/library/cleanup-bundles', method: 'POST' },
  cleanExports: { path: '/api/library/clean-exports', method: 'POST' },
  openDownloadDir: { path: '/api/open/download-dir', method: 'POST' },
  openExtractedDir: { path: '/api/open/extracted-dir', method: 'POST' },
  openWallpapersDir: { path: '/api/open/wallpapers-dir', method: 'POST' },
  listDownloaded: { path: '/api/library/downloaded', method: 'GET' },
  updateMetadata: { path: '/api/metadata/update', method: 'POST' },
  syncWiki: { path: '/api/metadata/sync-wiki', method: 'POST' },
  exportWallpaper: { path: '/api/export', method: 'POST' },
  exportImage: { path: '/api/export-image', method: 'POST' },
  exportImageData: { path: '/api/export-image-data', method: 'POST' },
  applyWallpaper: { path: '/api/apply', method: 'POST' },
  getPalette: { path: '/api/palette', method: 'POST' },
  getConfig: { path: '/api/config', method: 'GET' },
  setConfig: { path: '/api/config', method: 'POST' },
  getUpscaleVersions: { path: '/api/upscale/versions', method: 'GET' },
  upscaleSkin: { path: '/api/upscale', method: 'POST' },
  deleteUpscaleVersion: { path: '/api/upscale/delete', method: 'POST' },
  voiceLines: { path: '/api/voice/lines', method: 'GET' },
  syncVoiceLines: { path: '/api/voice/sync-lines', method: 'POST' },
}

// pywebview 侧的方法名（Python snake_case）
const PY_API_NAMES = {
  openDownloadDir: 'open_download_dir',
  openExtractedDir: 'open_extracted_dir',
}

function bodyFor(name, args) {
  if (name === 'downloadSkin' || name === 'deleteSkin') {
    const [character, bundle, skinName, downloadId] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', download_id: downloadId || '' }
  }
  if (name === 'exportImage') {
    const [character, bundle, skinName, upscaleVersion] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', upscaleVersion: upscaleVersion || 'none' }
  }
  if (name === 'exportWallpaper' || name === 'applyWallpaper') {
    const [character, bundle, skinName, options] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', options: options || {} }
  }
  if (name === 'upscaleSkin') {
    const [character, bundle, skinName, options] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', ...(options || {}) }
  }
  if (name === 'deleteUpscaleVersion') {
    const [character, bundle, skinName, version] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', version: version || '' }
  }
  if (name === 'syncVoiceLines') {
    const [characters] = args
    return { characters: characters || [] }
  }
  if (name === 'getPalette') {
    const [character, bundle, skinName, opts] = args
    return {
      character: character || '',
      bundle: bundle || '',
      name: skinName || '',
      mode: (opts && opts.mode) || 'auto',
      bgColor: opts && opts.bgColor ? opts.bgColor : undefined,
    }
  }
  if (name === 'cancelDownload') {
    const [id] = args
    return { download_id: id || '' }
  }
  if (name === 'exportImageData') {
    const [character, bundle, skinName, dataUrl, index] = args
    return { character: character || '', bundle: bundle || '', name: skinName || '', dataUrl: dataUrl || '', index: index || null }
  }
  if (name === 'setConfig') {
    const [cfg] = args
    return { config: cfg || {} }
  }
  // 批量删除：args[0] = [{character,bundle,name}, ...]
  if (name === 'deleteSkinsBatch') {
    const [items] = args
    return { items: items || [] }
  }
  return {}
}

async function callBackend(name, args) {
  const cfg = API_ACTIONS[name]
  const opts = { method: cfg.method, headers: { 'Content-Type': 'application/json' } }
  let url = `${API_BASE}${cfg.path}`
  if (name === 'getUpscaleVersions' || name === 'voiceLines') {
    const [character, bundle, skinName] = args
    const params = new URLSearchParams()
    if (character) params.set('character', character)
    if (bundle) params.set('bundle', bundle)
    if (skinName) params.set('name', skinName)
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }
  if (cfg.method === 'POST') {
    opts.body = JSON.stringify(bodyFor(name, args))
  }
  const resp = await fetch(url, opts)
  // 先检查 HTTP 状态再解析 JSON：后端返回错误页/空 body 时 json() 会抛
  // 解析异常，被上层误报成“后端服务未启动”，误导排障
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export const metadataUrl = (rel) =>
  import.meta.env.DEV
    ? DEV_RESOURCES
      ? `/@fs/${DEV_RESOURCES}/metadata/${rel}`
      : `resources/metadata/${rel}`
    : `resources/metadata/${rel}`
export const assetUrl = (rel) =>
  import.meta.env.DEV
    ? DEV_RESOURCES
      ? `/@fs/${DEV_RESOURCES}/${rel}`
      : `resources/${rel}`
    : `resources/${rel}`

// 下载阶段事件流（SSE）：下载资源/提取/同步索引等实时状态
export const sseUrl = () => `${API_BASE}/api/events`

async function call(name, ...args) {
  const mod = await import('../mock/api.js')
  // 数据方法始终读取本地元数据（浏览器/桌面通用）
  if (name === 'listCharacters' || name === 'getCharacter') return mod[name](...args)
  // 动作类：优先 pywebview 原生方法（桌面端不需要本地后端服务），否则走本地后端 API
  if (isPywebview()) {
    const py = window.pywebview && window.pywebview.api
    const pyName = PY_API_NAMES[name] || name
    if (py && typeof py[pyName] === 'function') {
      try {
        return await py[pyName](...args)
      } catch (e) {
        // 桌面桥接调用失败时返回统一错误结构，避免未捕获的 rejection 卡死界面状态
        return { ok: false, error: `桌面接口调用失败：${e.message || e}` }
      }
    }
  }
  if (API_ACTIONS[name]) {
    try {
      const result = await callBackend(name, args)
      if (['downloadSkin', 'deleteSkin', 'deleteSkinsBatch', 'clearDownloads', 'updateMetadata', 'syncWiki'].includes(name)) mod.invalidateCache()
      return result
    } catch (e) {
      // HTTP 状态错误（接口 500/404 等）与连接失败（服务未启动）区分开
      const isHttp = e.message && e.message.startsWith('HTTP ')
      return { ok: false, error: isHttp ? `后端接口错误：${e.message}` : `后端服务未启动（${API_BASE}）：${e.message}` }
    }
  }
  return mod[name](...args)
}

export const bridge = {
  isPywebview,
  listCharacters: () => call('listCharacters'),
  getCharacter: (id) => call('getCharacter', id),
  exportWallpaper: (character, bundle, name, options) => call('exportWallpaper', character, bundle, name, options),
  exportImage: (character, bundle, name, upscaleVersion) => call('exportImage', character, bundle, name, upscaleVersion),
  applyWallpaper: (character, bundle, name, options) => call('applyWallpaper', character, bundle, name, options),
  getPalette: (character, bundle, name, opts) => call('getPalette', character, bundle, name, opts),
  getUpscaleVersions: (character, bundle, name) => call('getUpscaleVersions', character, bundle, name),
  upscaleSkin: (character, bundle, name, options) => call('upscaleSkin', character, bundle, name, options),
  deleteUpscaleVersion: (character, bundle, name, version) => call('deleteUpscaleVersion', character, bundle, name, version),
  voiceLines: (character, bundle, name) => call('voiceLines', character, bundle, name),
  syncVoiceLines: (characters) => call('syncVoiceLines', characters),
  downloadSkin: (character, bundle, name, downloadId) => call('downloadSkin', character, bundle, name, downloadId),
  cancelDownload: (id) => call('cancelDownload', id),
  exportImageData: (character, bundle, name, dataUrl, index) => call('exportImageData', character, bundle, name, dataUrl, index),
  listDownloaded: () => call('listDownloaded'),
  deleteSkin: (character, bundle, name) => call('deleteSkin', character, bundle, name),
  deleteSkinsBatch: (items) => call('deleteSkinsBatch', items),
  clearDownloads: () => call('clearDownloads'),
  cleanupBundles: () => call('cleanupBundles'),
  cleanExports: () => call('cleanExports'),
  openDownloadDir: () => call('openDownloadDir'),
  openExtractedDir: () => call('openExtractedDir'),
  openWallpapersDir: () => call('openWallpapersDir'),
  updateMetadata: () => call('updateMetadata'),
  syncWiki: () => call('syncWiki'),
  getConfig: () => call('getConfig'),
  setConfig: (cfg) => call('setConfig', cfg),
}
