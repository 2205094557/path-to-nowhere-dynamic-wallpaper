// 数据层：从 resources/metadata 读取官方元数据（characters/skins）+ local_skins.json 标记本地已提取资源
import { metadataUrl } from '../bridge'

const delay = (ms) => new Promise((r) => setTimeout(r, ms))

function gradientFrom(name) {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0
  const hue = h % 360
  return [`hsl(${hue} 45% 34%)`, `hsl(${(hue + 45) % 360} 55% 12%)`]
}

let cache = null

export function invalidateCache() {
  cache = null
}

async function loadAll() {
  if (cache) return cache
  // 打包版静态服务器默认允许浏览器缓存（Last-Modified 启发式缓存）：
  // 下载/提取后 local_skins.json 会更新，若不禁用缓存会读到旧文件，
  // 导致"下载完但界面仍显示未下载、预览不出来"。统一 no-store。
  const noCache = { cache: 'no-store' }
  const unwrap = (v, key) => (Array.isArray(v) ? v : (v && Array.isArray(v[key]) ? v[key] : []))
  const [characters, skins, local] = await Promise.all([
    fetch(metadataUrl('characters.json'), noCache).then(r => r.json()).then(v => unwrap(v, 'characters')),
    fetch(metadataUrl('skins.json'), noCache).then(r => r.json()).then(v => unwrap(v, 'skins')),
    fetch(metadataUrl('local_skins.json'), noCache).then(r => r.json()).then(v => unwrap(v, 'local_skins')),
  ])
  const byCharacter = {}
  for (const c of characters) {
    byCharacter[c.name] = {
      ...c,
      id: c.name,
      gradient: gradientFrom(c.name),
      // PTN 图鉴字段：职业(profession)/稀有度(rarity)/星级(grade)；装束系列(series)在装束上
      profession: c.profession || '',
      rarity: c.rarity || '',
      grade: c.grade || '',
      skins: [],
    }
  }
  const localByPainting = {}
  for (const l of local) {
    if (l.painting && !(l.painting in localByPainting)) localByPainting[l.painting] = l
  }
  for (const sk of skins) {
    const character = byCharacter[sk.character]
    if (!character) continue
    // 本地资源只按 painting 精确匹配：同一 character+bundle 下可能挂多个装束，
    // 不能用 character+bundle 回退，否则会把基础装束的静态资源误挂到动态装束上。
    const loc = sk.painting ? (localByPainting[sk.painting] || null) : null
    character.skins.push({
      key: `${sk.character}|${sk.bundle}|${sk.name}`,
      character: sk.character,
      bundle: sk.bundle,
      name: sk.name,
      painting: sk.painting || '',
      series: sk.series || '',
      resourceCategory: sk.resource_category || (sk.type === 'live2d' ? 'live2d' : ''),
      resourceGroup: sk.resource_group || (sk.type === 'live2d' ? 'dynamic' : 'base'),
      resourceVariant: sk.resource_variant || 'main',
      displayLabel: sk.display_label || sk.name,
      sortOrder: Number(sk.sort_order || 999),
      internalModel: sk.internal_model || '',
      source: sk.source || '',
      type: loc ? loc.type : sk.type || 'unknown',
      status: loc ? 'downloaded' : 'remote',
      asset: loc ? loc.asset : null,
    })
  }
  for (const character of Object.values(byCharacter)) {
    character.skins.sort((a, b) => a.sortOrder - b.sortOrder || a.displayLabel.localeCompare(b.displayLabel, 'zh-Hans-CN'))
  }
  cache = Object.values(byCharacter).sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
  return cache
}

export async function listCharacters() {
  await delay(150)
  return (await loadAll()).map((c) => ({ ...c, skins: c.skins }))
}

export async function getCharacter(id) {
  await delay(100)
  return (await loadAll()).find((c) => c.name === id || c.id === String(id)) || null
}
