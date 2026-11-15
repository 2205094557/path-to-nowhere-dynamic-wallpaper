// 前端 motionRules.js 与 Python core/interaction.py 的等价性校验。
//
// 两侧必须逐条一致：JS 版供预览页与导出壁纸运行时使用，Python 版供导出器与测试使用。
// 这里不仅断言几条手写用例，还会把 resources/extracted/live2d 下所有模型都算一遍，
// 与 Python 的输出做全量 diff —— 只改一侧的规则会立刻在这里炸掉。
//
// 运行：node tests/motion_rules.test.mjs

import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'

import { classifyMotion, buildInteractSeq } from '../frontend/src/features/preview/motionRules.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const MODEL_ROOT = path.join(ROOT, 'resources', 'extracted', 'live2d')

// 与 core/interaction.py::motion_labels 同序抽取动作 stem
function motionLabels(meta) {
  const motions = meta?.FileReferences?.Motions || meta?.Motions || {}
  const groups = Object.keys(motions).sort((a, b) => (a === 'All' ? -1 : b === 'All' ? 1 : 0))
  const labels = []
  const seen = new Set()
  for (const group of groups) {
    for (const item of motions[group] || []) {
      const file = String(item?.File || '')
      const identity = file.replace(/\\/g, '/').toLowerCase()
      if (!file || seen.has(identity)) continue
      seen.add(identity)
      let stem = file.split('/').pop()
      if (stem.toLowerCase().endsWith('.motion3.json')) stem = stem.slice(0, -'.motion3.json'.length)
      if (stem && !labels.includes(stem)) labels.push(stem)
    }
  }
  return labels
}

let failures = 0
function test(name, fn) {
  try {
    fn()
    console.log(`ok   ${name}`)
  } catch (err) {
    failures += 1
    console.log(`FAIL ${name}\n     ${String(err.message).split('\n').join('\n     ')}`)
  }
}

test('固定语义分类', () => {
  const cases = {
    Greet: 'greet', clip_ll_greet: 'greet',
    Boring: 'boring', clip_airuier_boring: 'boring',
    Level: 'level', clip_ll_level: 'level',
    Idle: 'standby', Background: 'standby', 'Background+idle': 'standby',
    IdleCombined: 'standby', Exitidle: 'standby',
    Click_Head: 'head', Click_Face: 'head', clip_ll_click_head: 'head',
    Click_Body: 'touch', Click_Flower: 'touch', Click_Sword: 'touch', Click_Wine: 'touch',
    Click: 'touch',
    Click_Interactivearea: 'ignore', Click_Background: 'ignore',
    Drag_End_Sword: 'ignore', Exit: 'ignore', Prepare: 'ignore', Upgrade: 'ignore', '': 'ignore',
  }
  for (const [label, expected] of Object.entries(cases)) {
    assert.equal(classifyMotion(label), expected, `${label} 应为 ${expected}`)
  }
})

test('普通触摸无人工覆盖，全部走顺序轮换', () => {
  const seq = buildInteractSeq(['Boring', 'Click_Body', 'Click_Head', 'Level'])
  const byLabel = Object.fromEntries(seq.map((s) => [s.label, s]))
  assert.equal(byLabel.Click_Body.preferred, '')
  assert.equal(byLabel.Click_Head.preferred, '摸头')
  assert.equal(byLabel.Level.preferred, '升级')
  assert.equal(byLabel.Boring.category, 'idle')
  assert.equal(byLabel.Boring.preferred, '')
  assert.equal(seq.length, 4)
})

test('二阶段分类（stage2=true）', () => {
  const cases = {
    Click_Interactivearea: 'touch',
    Click_Interactivearea2: 'touch',
    Exitidle: 'interact_exit',
    Click_Head: 'head',
    Click_Body: 'touch',
    Click_Background: 'ignore',
    Drag_End_Head: 'ignore',
  }
  for (const [label, expected] of Object.entries(cases)) {
    assert.equal(classifyMotion(label, true), expected, `${label} stage2 应为 ${expected}`)
  }
  // 一阶段行为保持不变
  assert.equal(classifyMotion('Click_Interactivearea'), 'ignore')
  assert.equal(classifyMotion('Exitidle'), 'standby')
})

test('二阶段序列（Nuokesi_501）', () => {
  if (!existsSync(path.join(MODEL_ROOT, 'Nuokesi_501', 'model3.json'))) return
  const meta = JSON.parse(readFileSync(path.join(MODEL_ROOT, 'Nuokesi_501', 'model3.json'), 'utf8'))
  const byLabel = Object.fromEntries(buildInteractSeq(motionLabels(meta), true).map((s) => [s.label, s]))
  assert.equal(byLabel.Exitidle.category, 'interact_exit')
  assert.equal(byLabel.Exitidle.preferred, '退出互动')
  assert.equal(byLabel.Click_Head.preferred, '摸头')
  for (const label of ['Click_Body', 'Click_Handright', 'Click_Interactivearea']) {
    assert.equal(byLabel[label].category, 'interact', label)
    assert.equal(byLabel[label].preferred, '', label)
  }
  // 一阶段同模型：Interactivearea/Exitidle 不进序列
  const labels1 = buildInteractSeq(motionLabels(meta)).map((s) => s.label)
  assert.ok(!labels1.includes('Click_Interactivearea'))
  assert.ok(!labels1.includes('Exitidle'))
})

if (existsSync(MODEL_ROOT)) {
  const models = readdirSync(MODEL_ROOT, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .filter((name) => existsSync(path.join(MODEL_ROOT, name, 'model3.json')))

  const jsSequences = {}
  for (const name of models) {
    const meta = JSON.parse(readFileSync(path.join(MODEL_ROOT, name, 'model3.json'), 'utf8'))
    jsSequences[name] = buildInteractSeq(motionLabels(meta))
  }

  test('全量模型与 Python 实现逐个动作一致', () => {
    const script = [
      'import json, sys',
      'sys.path.insert(0, ".")',
      'from pathlib import Path',
      'from core.interaction import build_interact_seq, motion_labels',
      'root = Path("resources/extracted/live2d")',
      'out = {}',
      'for p in sorted(root.glob("*/model3.json")):',
      '    out[p.parent.name] = build_interact_seq(motion_labels(json.loads(p.read_text(encoding="utf-8"))))',
      // ensure_ascii=True：用 \uXXXX 转义输出，彻底绕开 Windows 控制台代码页
      // 对中文的破坏（否则「摸头」会被读成乱码导致假失败）
      'print(json.dumps(out))',
    ].join('\n')
    // Windows 控制台默认 GBK：固定用 UTF-8 管道，否则「摸头/升级」会被拆成乱码
    const raw = execFileSync('python', ['-I', '-c', script], {
      cwd: ROOT,
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    const pySequences = JSON.parse(raw)
    assert.deepEqual(Object.keys(jsSequences).sort(), Object.keys(pySequences).sort())
    for (const name of Object.keys(pySequences)) {
      assert.deepEqual(jsSequences[name], pySequences[name], `${name} 的互动序列两端不一致`)
    }
  })

  test('二阶段 head_interact 例外（Nuokesi_501）', () => {
  if (!existsSync(path.join(MODEL_ROOT, 'Nuokesi_501', 'model3.json'))) return
  const meta = JSON.parse(readFileSync(path.join(MODEL_ROOT, 'Nuokesi_501', 'model3.json'), 'utf8'))
  const labels = motionLabels(meta)
  const withHead = Object.fromEntries(buildInteractSeq(labels, true, true).map((s) => [s.label, s]))
  assert.equal(withHead.Click_Head.category, 'interact')
  assert.equal(withHead.Click_Head.preferred, '')
  // 无标记时仍固定摸头
  const withoutHead = Object.fromEntries(buildInteractSeq(labels, true).map((s) => [s.label, s]))
  assert.equal(withoutHead.Click_Head.preferred, '摸头')
  // 一阶段不受标记影响
  assert.equal(classifyMotion('Click_Head', false, true), 'head')
})

test('全量模型 stage2=true 与 Python 实现逐个动作一致', () => {
    const script = [
      'import json, sys',
      'sys.path.insert(0, ".")',
      'from pathlib import Path',
      'from core.interaction import build_interact_seq, motion_labels',
      'root = Path("resources/extracted/live2d")',
      'out = {}',
      'for p in sorted(root.glob("*/model3.json")):',
      '    meta = json.loads(p.read_text(encoding="utf-8"))',
      '    out[p.parent.name] = build_interact_seq(motion_labels(meta), stage2=True)',
      'print(json.dumps(out))',
    ].join('\n')
    const raw = execFileSync('python', ['-I', '-c', script], {
      cwd: ROOT,
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    const pySequences = JSON.parse(raw)
    for (const name of Object.keys(pySequences)) {
      const meta = JSON.parse(readFileSync(path.join(MODEL_ROOT, name, 'model3.json'), 'utf8'))
      assert.deepEqual(
        buildInteractSeq(motionLabels(meta), true),
        pySequences[name],
        `${name} 的二阶段互动序列两端不一致`,
      )
    }
  })

  test('全量模型 stage2+head_interact 与 Python 实现逐个动作一致', () => {
    const script = [
      'import json, sys',
      'sys.path.insert(0, ".")',
      'from pathlib import Path',
      'from core.interaction import build_interact_seq, motion_labels',
      'root = Path("resources/extracted/live2d")',
      'out = {}',
      'for p in sorted(root.glob("*/model3.json")):',
      '    meta = json.loads(p.read_text(encoding="utf-8"))',
      '    out[p.parent.name] = build_interact_seq(motion_labels(meta), stage2=True, head_interact=True)',
      'print(json.dumps(out))',
    ].join('\n')
    const raw = execFileSync('python', ['-I', '-c', script], {
      cwd: ROOT,
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    const pySequences = JSON.parse(raw)
    for (const name of Object.keys(pySequences)) {
      const meta = JSON.parse(readFileSync(path.join(MODEL_ROOT, name, 'model3.json'), 'utf8'))
      assert.deepEqual(
        buildInteractSeq(motionLabels(meta), true, true),
        pySequences[name],
        `${name} 的二阶段(head_interact)互动序列两端不一致`,
      )
    }
  })

  test('关键模型的序列形状', () => {
    assert.deepEqual(
      jsSequences.Augustus_3.map((s) => s.label),
      ['Boring', 'Click_Body1', 'Click_Body2', 'Click_Flower', 'Click_Head', 'Click_Sword', 'Level'],
    )
    assert.deepEqual(jsSequences.Anarkia_3.map((s) => s.label), ['Click_Body'])
    assert.deepEqual(jsSequences.Hestia_4, [])
  })
} else {
  console.log('skip 缺少 resources/extracted/live2d 模型样本')
}

if (failures) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
