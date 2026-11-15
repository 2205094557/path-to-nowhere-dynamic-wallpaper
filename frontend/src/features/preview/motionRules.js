// 点击互动的动作分类与序列构建——JS 版规则，与 core/interaction.py 必须逐条等价。
//
// 本文件需要绝对纯净：不 import 任何模块、不访问 document/window，因为
//   - frontend/src/components/Live2DPreview.vue（预览端）import 它；
//   - templates/live2d_app_src.js（导出壁纸运行时）也 import 它；
//   - tests/motion_rules.test.mjs 直接 node 跑它。
// 任何 DOM 依赖都会让导出模板构建出带浏览器 API 的包。
//
// 改规则时必须同时改 core/interaction.py，并跑 tests/test_interaction.py 与
// tests/motion_rules.test.mjs —— 两侧用例表相同。

export const GREET = 'greet'
export const STANDBY = 'standby'
export const BORING = 'boring'
export const LEVEL = 'level'
export const HEAD = 'head'
export const TOUCH = 'touch'
export const INTERACT = 'interact'
export const INTERACT_EXIT = 'interact_exit'
export const IGNORE = 'ignore'

// 待机动作：Exitidle 仅在二阶段（stage2=true）例外，见 classifyMotion
const STANDBY_RE = /^(idle|background|idlecombined|background\+idle|exitidle)$/i
const HEAD_RE = /head|face/i
const CLICK_PREFIX_RE = /^(?:clip_\w+_)?click/i
// 含义无法确定的点击区域：命中即排除出点击序列。
// 具名部位（Flower/Sword/Wine/Bed/Back…）不算含糊，它们就是游戏里的触摸热区；
// 只有「互动区 / 背景点击」这种没有触摸语义的动作才排除。
const AMBIGUOUS_CLICK_RE = /interactive|background/i
// 四星皮二阶段的互动退出动作
const EXITIDLE_RE = /^exitidle$/i

// 动作文件名 → GREET / STANDBY / BORING / LEVEL / HEAD / TOUCH / INTERACT / INTERACT_EXIT / IGNORE
// stage2=true（语音库 interact 池非空）时的差异：Click_Interactivearea/N 算触摸序列，
// Exitidle 归 INTERACT_EXIT；其余与一阶段完全一致。
// headInteract=true（装束级例外，语音库 head_click_interact=true）：二阶段下
// Click_Head 不固定摸头，进 interact 轮换。
export function classifyMotion(label, stage2 = false, headInteract = false) {
  const stem = String(label || '').trim()
  if (!stem) return IGNORE
  // Greet 允许 clip_<角色>_greet 前缀，但绝不能宽松到把 `Upgrade` 当问候
  if (/(?:^|[_\s-])greet$/i.test(stem)) return GREET
  if (stage2 && EXITIDLE_RE.test(stem)) return INTERACT_EXIT
  if (STANDBY_RE.test(stem)) return STANDBY
  if (/boring/i.test(stem)) return BORING
  if (/level/i.test(stem)) return LEVEL
  if (CLICK_PREFIX_RE.test(stem)) {
    if (AMBIGUOUS_CLICK_RE.test(stem)) {
      // 二阶段互动里 Click_Interactivearea/N 就是互动点击热区（背景点击仍排除）
      if (stage2 && /interactive/i.test(stem)) return TOUCH
      return IGNORE
    }
    if (HEAD_RE.test(stem) && !(stage2 && headInteract)) return HEAD
    return TOUCH
  }
  // Drag_End_* / Exit / Prepare / Upgrade / 裸 Click / 其他未知动作：不进互动序列
  return IGNORE
}

// 摸头动作固定绑定「摸头」台词；普通触摸返回空串（由调用方按池内顺序轮换）
export function touchActionFor(label, headAction = '摸头') {
  return classifyMotion(label) === HEAD ? headAction : ''
}

// 按传入顺序（model3.json 文件顺序）构建点击互动序列
// 返回 [{label, category, preferred}]，preferred 为空串表示池内轮换
export function buildInteractSeq(motions, stage2 = false, headInteract = false) {
  const seq = []
  for (const label of motions || []) {
    const kind = classifyMotion(label, stage2, headInteract)
    if (kind === GREET || kind === STANDBY || kind === IGNORE) continue
    // head 也落在 touch 池，实际取词由 preferred 锁定
    let category = kind
    if (kind === HEAD) category = TOUCH
    else if (kind === BORING) category = 'idle'
    else if (stage2 && kind === TOUCH) category = INTERACT // 二阶段：非摸头点击走互动池
    // preferred 只在 HEAD（固定摸头）/LEVEL/INTERACT_EXIT 上锁定；headInteract
    // 时 kind 不可能是 HEAD，Click_Head 自然落进 interact 轮换
    let ruleAction = kind === HEAD ? '摸头' : ''
    if (kind === LEVEL) ruleAction = ruleAction || '升级'
    else if (kind === INTERACT_EXIT) ruleAction = '退出互动'
    seq.push({ label, category, preferred: ruleAction })
  }
  return seq
}
