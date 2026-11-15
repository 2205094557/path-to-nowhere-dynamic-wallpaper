# -*- coding: utf-8 -*-
"""点击互动规则的回归测试。

规则的事实来源是 core/interaction.py；前端 motionRules.js 必须逐条等价，
由 tests/motion_rules.test.mjs 用同一组用例校验。这里的断言尽量贴住真实
model3.json（resources/extracted/live2d 下 110 个模型），而不是手写玩具数据。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.interaction import (  # noqa: E402
    BORING,
    GREET,
    HEAD,
    IGNORE,
    INTERACT,
    INTERACT_EXIT,
    LEVEL,
    STANDBY,
    TOUCH,
    build_interact_seq,
    classify_motion,
    motion_labels,
)

MODEL_ROOT = ROOT / "resources" / "extracted" / "live2d"


def load_labels(model_name: str) -> list[str]:
    meta = json.loads((MODEL_ROOT / model_name / "model3.json").read_text(encoding="utf-8"))
    return motion_labels(meta)


def sequence(model_name: str) -> list[dict]:
    return build_interact_seq(load_labels(model_name))


def labels_of(model_name: str) -> list[str]:
    return [step["label"] for step in sequence(model_name)]


class ClassifyMotionTests(unittest.TestCase):
    def test_fixed_semantics(self) -> None:
        cases = {
            "Greet": GREET,
            "clip_ll_greet": GREET,
            "Boring": BORING,
            "clip_airuier_boring": BORING,
            "Level": LEVEL,
            "clip_ll_level": LEVEL,
            "Idle": STANDBY,
            "Background": STANDBY,
            "Background+idle": STANDBY,
            "IdleCombined": STANDBY,
            "Exitidle": STANDBY,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(classify_motion(label), expected)

    def test_head_variants(self) -> None:
        for label in ("Click_Head", "Click_Face", "Click_Head2", "clip_ll_click_head"):
            with self.subTest(label=label):
                self.assertEqual(classify_motion(label), HEAD)

    def test_body_click_variants_are_touch(self) -> None:
        # 具名部位（Flower/Sword/Wine/Bed/Back）只是装饰，仍然对应普通触摸
        for label in (
            "Click_Body", "Click_Body1", "Click_Body2", "Click_Hand", "Click_HandLeft",
            "Click_HandRight", "Click_Leg1", "Click_Leg2", "Click_Flower", "Click_Sword",
            "Click_Wine", "Click_Bed", "Click_Back", "clip_ll_click_body",
            "clip_airuier_click_body2",
        ):
            with self.subTest(label=label):
                self.assertEqual(classify_motion(label), TOUCH)

    def test_ambiguous_actions_ignored(self) -> None:
        for label in (
            "Click_Interactivearea", "Click_Background",
            "Drag_End_Sword", "Drag_End_Cam1", "Drag_End_Dress", "Drag_End_Lotus",
            "Exit", "Prepare", "Upgrade", "clip_airuier_upgrade", "",
        ):
            with self.subTest(label=label):
                self.assertEqual(classify_motion(label), IGNORE)

    def test_bare_click_is_touch(self) -> None:
        # hypatia_4 只有一个裸 `Click`：它就是这款模型唯一的触摸反馈，
        # 排除掉会让整款皮肤完全没有点击反应。
        self.assertEqual(classify_motion("Click"), TOUCH)


class Stage2MotionTests(unittest.TestCase):
    """四星皮二阶段（语音库 interact 池非空时启用）的动作规则。

    游戏语义：进入二阶段互动后 Greet 播互动1；点击热区（含 Click_Interactivearea/N）
    按顺序播互动2..N，Click_Head 仍是摸头；Exitidle 播退出互动。
    一阶段分类行为必须与旧版逐条等价（stage2=False）。
    """

    def test_stage2_classification(self) -> None:
        cases = {
            "Click_Interactivearea": TOUCH,
            "Click_Interactivearea2": TOUCH,
            "Exitidle": INTERACT_EXIT,
            "Click_Head": HEAD,
            "Click_Body": TOUCH,
            "Click_Background": IGNORE,  # 背景点击依旧不是互动热区
            "Drag_End_Head": IGNORE,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(classify_motion(label, stage2=True), expected)

    def test_stage1_classification_unchanged(self) -> None:
        for label in ("Click_Interactivearea", "Click_Interactivearea2"):
            self.assertEqual(classify_motion(label), IGNORE)
        self.assertEqual(classify_motion("Exitidle"), STANDBY)
        self.assertEqual(classify_motion("Exitidle", stage2=False), STANDBY)

    def test_head_interact_exception(self) -> None:
        # 画中晨曦二阶段（head_click_interact=true）：wiki 页摸头台词是一阶段风格，
        # 二阶段语言风格不同，所以 Click_Head 不固定摸头，同样进互动轮换
        seq = build_interact_seq(load_labels("Nuokesi_501"), stage2=True, head_interact=True)
        by_label = {step["label"]: step for step in seq}
        self.assertEqual(by_label["Click_Head"]["category"], "interact")
        self.assertEqual(by_label["Click_Head"]["preferred"], "")
        # 无该标记时 Click_Head 仍固定摸头
        default = {step["label"]: step for step in build_interact_seq(load_labels("Nuokesi_501"), stage2=True)}
        self.assertEqual(default["Click_Head"]["preferred"], "摸头")
        # 一阶段不受该标记影响（标记只在二阶段生效）
        self.assertEqual(classify_motion("Click_Head", stage2=False, head_interact=True), HEAD)

    @unittest.skipUnless((MODEL_ROOT / "Nuokesi_501").is_dir(), "缺少 Nuokesi_501 模型样本")
    def test_nuokesi_501_stage2_sequence(self) -> None:
        seq = build_interact_seq(load_labels("Nuokesi_501"), stage2=True)
        by_label = {step["label"]: step for step in seq}
        # 二阶段：Interactivearea 进轮换、Exitidle 播退出互动
        self.assertIn("Click_Interactivearea", by_label)
        self.assertIn("Exitidle", by_label)
        self.assertEqual(by_label["Exitidle"]["category"], "interact_exit")
        self.assertEqual(by_label["Exitidle"]["preferred"], "退出互动")
        self.assertEqual(by_label["Click_Head"]["preferred"], "摸头")
        # 该模型清单里只有 Body/Handright/Interactivearea 三个非摸头点击，
        # 全部落 interact 池按顺序轮换（互动2→互动3→互动4→循环）
        for label in ("Click_Body", "Click_Handright", "Click_Interactivearea"):
            self.assertEqual(by_label[label]["category"], "interact", label)
            self.assertEqual(by_label[label]["preferred"], "", label)
        # 一阶段同模型：Interactivearea/Exitidle 不进序列
        seq1 = build_interact_seq(load_labels("Nuokesi_501"))
        labels1 = [step["label"] for step in seq1]
        self.assertNotIn("Click_Interactivearea", labels1)
        self.assertNotIn("Exitidle", labels1)

    @unittest.skipUnless((MODEL_ROOT / "Nuokesi_501").is_dir(), "缺少 Nuokesi_501 模型样本")
    def test_exporter_copies_head_interact_flag(self) -> None:
        from plugins.exporters.wallpaper_live2d import copy_voice_assets, build_interact_seq_for_export
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        skin = {
            "character": "诺克斯",
            "name": "诺克斯 装束 501",
            "painting": "nuokesi_501",
            "asset": {"dir": "extracted/live2d/Nuokesi_501", "model": "model3.json"},
        }
        meta = copy_voice_assets(skin, {"root": str(ROOT)}, tmp)
        self.assertTrue(meta["lines"].get("head_click_interact"))
        model_path = MODEL_ROOT / "Nuokesi_501" / "model3.json"
        seq = build_interact_seq_for_export(
            model_path,
            stage2=bool(meta["lines"].get("interact")),
            head_interact=bool(meta["lines"].get("head_click_interact")),
        )
        head_step = next(s for s in seq if s["label"] == "Click_Head")
        self.assertEqual(head_step["category"], "interact")
        self.assertEqual(head_step["preferred"], "")


class BuildInteractSeqTests(unittest.TestCase):
    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_augustus_3_flower_sword_counts_as_touch(self) -> None:
        # Drag_End_Sword 是拖拽收尾，不能当作触摸；Click_Flower/Sword 是触摸。
        self.assertEqual(
            labels_of("Augustus_3"),
            ["Boring", "Click_Body1", "Click_Body2", "Click_Flower", "Click_Head", "Click_Sword", "Level"],
        )
        by_label = {step["label"]: step for step in sequence("Augustus_3")}
        self.assertEqual(by_label["Click_Head"]["preferred"], "摸头")
        self.assertEqual(by_label["Click_Head"]["category"], "touch")
        self.assertEqual(by_label["Click_Flower"]["category"], "touch")
        self.assertEqual(by_label["Click_Sword"]["category"], "touch")
        self.assertEqual(by_label["Level"]["preferred"], "升级")

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_coquelicot_5_ignores_exit_and_prepare(self) -> None:
        self.assertEqual(
            labels_of("Coquelicot_5"),
            ["Boring", "Click_Body", "Click_HandLeft", "Click_HandRight", "Click_Head", "Click_Leg", "Level"],
        )

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_single_click_body_model(self) -> None:
        # 31 个皮肤只有 Click_Body，序列长度必须是 1，不能凭空补动作
        self.assertEqual(labels_of("Anarkia_3"), ["Click_Body"])

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_clip_prefixed_models(self) -> None:
        seq = sequence("LL_2")
        self.assertIn("clip_ll_click_head", [step["label"] for step in seq])
        head = next(step for step in seq if step["label"] == "clip_ll_click_head")
        self.assertEqual(head["preferred"], "摸头")
        # Upgrade 与 Level 重复，不参与点击序列
        airuier = labels_of("Airuier_3")
        self.assertNotIn("clip_airuier_upgrade", airuier)
        self.assertIn("clip_airuier_level", airuier)

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_model_without_motions(self) -> None:
        self.assertEqual(sequence("Hestia_4"), [])

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_no_per_skin_override_remains(self) -> None:
        # 可可莉克·绯夜初醒曾有一条手工映射（Click_Body→特别关注），已随覆盖通道一起删除：
        # 所有普通触摸都应走顺序轮换（preferred 为空串）。
        for step in sequence("Coquelicot_5"):
            if step["category"] == "touch" and step["preferred"] != "摸头":
                with self.subTest(label=step["label"]):
                    self.assertEqual(step["preferred"], "")

    @unittest.skipUnless(MODEL_ROOT.is_dir(), "缺少 resources/extracted/live2d 模型样本")
    def test_no_drag_or_control_motions_leak_into_sequence(self) -> None:
        banned = ("drag_end", "exit", "prepare", "upgrade", "interactivearea", "click_background")
        for model_json in MODEL_ROOT.glob("*/model3.json"):
            labels = motion_labels(json.loads(model_json.read_text(encoding="utf-8")))
            for step in build_interact_seq(labels):
                lowered = step["label"].casefold()
                with self.subTest(model=model_json.parent.name, label=step["label"]):
                    self.assertFalse(any(token in lowered for token in banned))


if __name__ == "__main__":
    unittest.main()
