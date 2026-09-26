"""utils/ui/aux_tools.py 的单测：测量点 / 参照物的状态与文字（纯逻辑，不画图）。"""
from unittest import TestCase

from utils.ui import aux_tools
from utils.ui.aux_tools import AuxState


class MeasureTest(TestCase):
    def test_clicks_idle_without_mode(self):
        aux = AuxState()
        self.assertEqual(aux_tools.CLICK_IDLE, aux.click("preview", (1, 2)))
        self.assertEqual([], aux.points["preview"])

    def test_two_clicks_make_one_measurement(self):
        aux = AuxState()
        aux.set_mode("measure")
        self.assertEqual(aux_tools.CLICK_OK, aux.click("preview", (10, 20)))
        self.assertEqual(1, len(aux.points["preview"]))
        self.assertIn("再点一下", aux.measure_text())
        aux.click("preview", (40, 60))
        rows = aux.measure_rows()
        self.assertEqual(2, len(rows))
        self.assertIn("距离 50px", rows[0])
        self.assertIn("Δx +30 / Δy +40", rows[0])
        self.assertIn("(10, 20) → (40, 60)", rows[1])

    def test_third_click_starts_over(self):
        aux = AuxState()
        aux.set_mode("measure")
        aux.click("preview", (0, 0))
        aux.click("preview", (10, 0))
        aux.click("preview", (100, 100))
        self.assertEqual([(100.0, 100.0)], aux.points["preview"])

    def test_entering_measure_mode_clears_old_points(self):
        aux = AuxState()
        aux.click("preview", (0, 0))
        aux.set_mode("measure")
        self.assertEqual([], aux.points["preview"])
        self.assertEqual([], aux.measure_rows())

    def test_measure_text_names_the_canvas(self):
        aux = AuxState()
        aux.set_mode("measure")
        aux.click("cover", (0, 0))
        aux.click("cover", (3, 4))
        self.assertTrue(aux.measure_text().startswith("封面："))
        self.assertIn("距离 5px", aux.measure_text())

    def test_clear_measure(self):
        aux = AuxState()
        aux.set_mode("measure")
        aux.click("preview", (0, 0))
        aux.clear_measure()
        self.assertEqual([], aux.points["preview"])
        self.assertEqual([], aux.measure_rows())

    def test_canvases_are_independent(self):
        aux = AuxState()
        aux.set_mode("measure")
        aux.click("preview", (0, 0))
        aux.click("preview", (10, 0))
        aux.click("cover", (0, 0))
        self.assertEqual(2, len(aux.points["preview"]))
        self.assertEqual(1, len(aux.points["cover"]))
        self.assertEqual("cover", aux.last_space)
        self.assertTrue(aux.measure_text("preview").startswith("预览台："))
        self.assertIn("再点一下", aux.measure_text("cover"))


class GuideTest(TestCase):
    def test_place_guide_uses_pending_settings(self):
        aux = AuxState()
        aux.settings.update(kind="rect", angle=30, w=100, h=50)
        aux.set_mode("guide")
        self.assertEqual(aux_tools.CLICK_OK, aux.click("cover", (7, 8)))
        guide = aux.guides[0]
        self.assertEqual({"kind": "rect", "angle": 30, "w": 100, "h": 50}, {
            key: guide[key] for key in ("kind", "angle", "w", "h")})
        self.assertEqual(("cover", 7.0, 8.0), (guide["space"], guide["x"], guide["y"]))

    def test_defaults_match_html_version(self):
        aux = AuxState()
        self.assertEqual(aux_tools.DEFAULT_GUIDE, aux.settings)
        self.assertEqual(("line", 0, 300, 120), (aux.settings["kind"], aux.settings["angle"],
                                                 aux.settings["w"], aux.settings["h"]))

    def test_guide_limit(self):
        aux = AuxState()
        aux.set_mode("guide")
        for index in range(aux_tools.MAX_GUIDES):
            self.assertEqual(aux_tools.CLICK_OK, aux.click("preview", (index, 0)))
        self.assertEqual(aux_tools.CLICK_FULL, aux.click("preview", (0, 0)))
        self.assertEqual(aux_tools.MAX_GUIDES, len(aux.guides))

    def test_clear_and_count_text(self):
        aux = AuxState()
        aux.set_mode("guide")
        aux.click("preview", (0, 0))
        aux.click("cover", (0, 0))
        self.assertIn("共 2 个参照物", aux.guide_text())
        self.assertIn("线段 2 个", aux.guide_text())
        aux.clear_guides()
        self.assertEqual([], aux.guides)
        self.assertEqual(f"共 0 个参照物（最多 {aux_tools.MAX_GUIDES} 个）", aux.guide_text())

    def test_switching_mode_keeps_data(self):
        aux = AuxState()
        aux.set_mode("guide")
        aux.click("preview", (0, 0))
        aux.set_mode("measure")
        aux.click("preview", (0, 0))
        self.assertEqual(1, len(aux.guides))
        self.assertEqual(1, len(aux.points["preview"]))
        # 退出工具模式后画的东西还在（方便对着尺寸看）
        aux.set_mode(None)
        self.assertEqual([(0.0, 0.0)], aux.points["preview"])
        self.assertEqual(1, len(aux.guides))
