"""点击互动的动作分类与序列构建（单一事实来源）。

游戏内的真实语义（由 110 个已提取模型的动作词汇表归纳）：

    Greet                 入场          → greet 池，固定取 action=问候
    Boring                点击序列一项   → idle 池（空则回落 boring 池）
    Level                 点击序列一项   → level 池，固定取 action=升级
    Upgrade               忽略（旧命名残留，与 Level 重复，见 clip_airuier_upgrade）
    Click_Head / _Face    点击序列一项   → touch 池，固定取 action 含「摸头」
    其余 Click_*          点击序列，按 model3.json 文件顺序依次推进
                                         → touch 池按顺序轮换

动作名里的具名部位（Flower / Sword / Wine / Bed …）只是装饰，位置顺序才决定它
对应「普通触摸N」中的哪一条。含义无法确定的动作（Drag_End_* / Exit / Prepare /
Click_Interactivearea / Click_Background）一律不进序列，否则会把「拖拽结束」
「互动区」「退出」当成触摸反馈。裸 Click 是模型唯一的点击反馈，仍算普通触摸。

四星皮二阶段（stage2=True，数据判据：语音库 interact 池非空）例外：
    Greet                 → interact 池，固定取 action=互动1（进入二阶段互动的第一个动作）
    其余 Click_*（含 Click_Interactivearea/N）→ interact 池按顺序轮换 互动2..N
                            （Click_Head 仍是摸头，不进互动轮换）
    Exitidle              → interact_exit 池，固定取 action=退出互动（退出二阶段互动）
    一阶段模型分类行为完全不变（stage2=False 与旧版逐条等价）。

装束级例外 head_interact=True（数据判据：语音库装束条目 head_click_interact=true，
目前仅画中晨曦二阶段 nuokesi_501——该皮 wiki 页的摸头台词是一阶段风格，二阶段
语言风格不同，播放会出戏）：二阶段下 Click_Head 不再固定摸头，同样进 interact
池按顺序轮换。

本模块是唯一权威实现；前端（frontend/src/features/preview/motionRules.js）与导出模板
（templates/live2d_app_src.js）各自持有等价的 JS 版本，由 tests/test_interaction.py
与 tests/motion_rules.test.mjs 用同一组用例校验，改规则时三处必须同步。
"""

from __future__ import annotations

import re

# 类别常量
GREET = "greet"
STANDBY = "standby"
BORING = "boring"
LEVEL = "level"
HEAD = "head"
TOUCH = "touch"
INTERACT = "interact"
INTERACT_EXIT = "interact_exit"
IGNORE = "ignore"

# 待机动作：模型级 Idle / Background 及其变体（Exitidle 仅在二阶段例外，见 classify_motion）
_STANDBY_RE = re.compile(r"^(idle|background|idlecombined|background\+idle|exitidle)$", re.IGNORECASE)
# 摸头：Click_Head / Click_Face
_HEAD_RE = re.compile(r"head|face", re.IGNORECASE)
# 点击动作前缀（含 clip_<角色>_click_* 变体）
_CLICK_PREFIX_RE = re.compile(r"^(?:clip_\w+_)?click", re.IGNORECASE)
# 含义无法确定的点击区域：命中即排除出点击序列。
# 具名部位（Flower/Sword/Wine/Bed/Back…）不算含糊，它们就是游戏里的触摸热区；
# 只有「互动区 / 背景点击」这种没有触摸语义的动作才排除。
_AMBIGUOUS_CLICK_RE = re.compile(r"interactive|background", re.IGNORECASE)
# 二阶段互动退出动作
_EXITIDLE_RE = re.compile(r"^exitidle$", re.IGNORECASE)


def classify_motion(label: str, stage2: bool = False, head_interact: bool = False) -> str:
    """把动作文件名归类到 GREET / STANDBY / BORING / LEVEL / HEAD / TOUCH / INTERACT / INTERACT_EXIT / IGNORE。

    stage2=True 时的差异：Click_Interactivearea/N 算触摸序列（游戏内二阶段互动的
    点击热区），Exitidle 归 INTERACT_EXIT；其余与一阶段完全一致。
    head_interact=True（仅二阶段）：Click_Head/Face 不固定摸头，进 interact 轮换。
    """
    stem = (label or "").strip()
    if not stem:
        return IGNORE
    # Greet 允许 clip_<角色>_greet 前缀，但绝不能宽松到把 `Upgrade` 当问候
    if re.search(r"(?:^|[_\s-])greet$", stem, re.IGNORECASE):
        return GREET
    if stage2 and _EXITIDLE_RE.match(stem):
        return INTERACT_EXIT
    if _STANDBY_RE.match(stem):
        return STANDBY
    if re.search(r"boring", stem, re.IGNORECASE):
        return BORING
    if re.search(r"level", stem, re.IGNORECASE):
        return LEVEL
    if _CLICK_PREFIX_RE.match(stem):
        if _AMBIGUOUS_CLICK_RE.search(stem):
            # 二阶段互动里 Click_Interactivearea/N 就是互动点击热区（背景点击仍排除）
            if stage2 and re.search(r"interactive", stem, re.IGNORECASE):
                return TOUCH
            return IGNORE
        if _HEAD_RE.search(stem) and not (stage2 and head_interact):
            return HEAD
        return TOUCH
    # Drag_End_* / Exit / Prepare / 裸 Clip / 其他未知动作：不进互动序列
    return IGNORE


def touch_action_for(label: str, head_action: str = "摸头") -> str:
    """摸头动作固定绑定「摸头」台词；普通触摸由调用方按池内顺序轮换。"""
    return head_action if classify_motion(label) == HEAD else ""


def motion_labels(model_meta: dict) -> list[str]:
    """按运行时同款顺序抽取 model3.json 的动作 stem。

    分组顺序与 templates/live2d_app_src.js 的 collectMotionEntries 一致：
    `All` 组优先，其余保持 model3.json 原始顺序；同名文件只保留首次出现。
    """
    motions = (model_meta or {}).get("FileReferences", {}).get("Motions") \
        or (model_meta or {}).get("Motions") or {}
    if not isinstance(motions, dict):
        return []
    groups = sorted(motions, key=lambda group: 0 if group == "All" else 1)
    labels: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in motions.get(group) or []:
            file = str((item or {}).get("File") or "")
            identity = file.replace("\\", "/").lower()
            if not file or identity in seen:
                continue
            seen.add(identity)
            stem = file.split("/")[-1]
            if stem.lower().endswith(".motion3.json"):
                stem = stem[: -len(".motion3.json")]
            if stem and stem not in labels:
                labels.append(stem)
    return labels


def build_interact_seq(motions: list[str], stage2: bool = False,
                       head_interact: bool = False) -> list[dict]:
    """按传入顺序（model3.json 文件顺序）构建点击互动序列。

    返回 [{label, category, preferred}]：
      category 为语音池分类（head 也落在 touch 池，实际取词由 preferred 锁定）；
      preferred 为规则推导出的固定台词 action，空串表示池内轮换。
    head_interact：二阶段装束级例外，Click_Head 也进 interact 轮换（见模块 docstring）。
    """
    seq: list[dict] = []
    for label in motions:
        kind = classify_motion(label, stage2=stage2, head_interact=head_interact)
        if kind in (GREET, STANDBY, IGNORE):
            continue
        if kind == HEAD:
            category = TOUCH
        elif kind == BORING:
            category = "idle"
        elif stage2 and kind == TOUCH:
            # 二阶段：非摸头点击全部走互动池（互动2..N 轮换），不读普通触摸
            category = INTERACT
        else:
            category = kind
        # preferred 只在 HEAD（固定摸头）/LEVEL/INTERACT_EXIT 上锁定；head_interact
        # 时 kind 不可能是 HEAD，Click_Head 自然落进 interact 轮换
        rule_action = "摸头" if kind == HEAD else ""
        if kind == LEVEL:
            rule_action = rule_action or "升级"
        elif kind == INTERACT_EXIT:
            rule_action = "退出互动"
        seq.append({"label": label, "category": category, "preferred": rule_action})
    return seq
