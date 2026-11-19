# -*- coding: utf-8 -*-
"""本地 OCR 读卡片（见 utils/chart_ocr.py）。"""
import unittest
from unittest import mock

from utils import chart_ocr as co


class ParseCardTest(unittest.TestCase):
    """`parse_card()` 是纯函数：直接把 OCR 文本行喂进去。"""

    def test_reads_rank_and_numbers_from_the_card(self):
        # 实测 rapidocr 读 憑歌ルク 那张卡的顺序就是这样（149 位 / TOTAL 164 / VIEW 103…）
        lines = ["149", "LAST WEEK new!!", "【新音源】中二病賛歌", "TOTAL 164", "憑歌ルク",
                 "VIEW 103", "得点 164 再生 103", "COMMENT 2", "MYLIST 3"]
        card = co.parse_card(lines)
        self.assertEqual("149", card["本周"])
        self.assertEqual("164", card["得点"])
        self.assertEqual("103", card["再生"])
        self.assertEqual("2", card["评论"])
        self.assertEqual("3", card["收藏"])
        self.assertNotIn("上周", card, "LAST WEEK new!! 就是新曲，不能写 new 进上周")

    def test_reads_last_week_rank(self):
        card = co.parse_card(["22", "LAST WEEK 18", "TOTAL 900", "VIEW 500"])
        self.assertEqual("22", card["本周"])
        self.assertEqual("18", card["上周"])

    def test_accepts_japanese_labels(self):
        card = co.parse_card(["44位", "得点 1120", "再生 780", "コメント 9", "マイリスト 20"])
        self.assertEqual("44", card["本周"])
        self.assertEqual("1120", card["得点"])
        self.assertEqual("780", card["再生"])
        self.assertEqual("9", card["评论"])
        self.assertEqual("20", card["收藏"])

    def test_label_lines_are_not_mistaken_for_the_rank(self):
        card = co.parse_card(["TOTAL 164", "VIEW 103"])
        self.assertNotIn("本周", card, "TOTAL 164 里的 164 不是名次")

    def test_rank_written_as_number_and_word(self):
        card = co.parse_card(["第149位", "TOTAL 100"])
        self.assertEqual("149", card["本周"])

    def test_label_and_number_on_separate_lines(self):
        card = co.parse_card(["149", "TOTAL", "164", "VIEW", "103"])
        self.assertEqual("164", card["得点"])
        self.assertEqual("103", card["再生"])

    def test_misread_labels_still_give_every_number(self):
        # 实测 rapidocr 把 VIEW 读成 VIEH / UIEH、MYLIST 读成 MVLIST
        card = co.parse_card(["1", "TOTAL", "3,516", "UIEH", "1,685", "COMMENT", "32",
                              "MVLIST", "90", "DATE", "2009 11 07", "sm8736774"])
        self.assertEqual("1", card["本周"])
        self.assertEqual("3516", card["得点"])
        self.assertEqual("1685", card["再生"])
        self.assertEqual("32", card["评论"])
        self.assertEqual("90", card["收藏"])

    def test_number_sequence_skips_noise_lines(self):
        self.assertEqual([3516, 1685, 32, 90],
                         co._number_sequence(["TOTAL", "3,516", "UIEH", "1,685",
                                              "COMMENT", "32", "2 %", "MVLIST", "90"]))


class CompleteTest(unittest.TestCase):
    def test_complete_card(self):
        self.assertTrue(co.is_complete({"本周": "44", "得点": "1120", "再生": "780"}))

    def test_missing_rank_is_not_complete(self):
        self.assertFalse(co.is_complete({"得点": "1120", "再生": "780"}))

    def test_only_one_number_is_not_complete(self):
        self.assertFalse(co.is_complete({"本周": "44", "得点": "1120"}))

    def test_point_total_below_a_component_is_rejected(self):
        # 得点 = 再生 + 评论权重 + 收藏权重，一定不小于任何一项；低于就是读错了
        self.assertFalse(co.is_complete({"本周": "44", "得点": "10", "再生": "780"}))


class EngineTest(unittest.TestCase):
    def tearDown(self):
        co.reset_engine()

    def test_available_only_probes_the_dependency(self):
        with mock.patch.object(co, "_name_available", return_value=None):
            self.assertFalse(co.available())
        with mock.patch.object(co, "_name_available", return_value="rapidocr"):
            self.assertTrue(co.available())

    def test_no_engine_means_no_reading_and_caller_falls_back(self):
        with mock.patch.object(co, "_name_available", return_value=None), \
             mock.patch.object(co, "_build_engine", return_value=(None, None)):
            co.reset_engine()
            self.assertFalse(co.available())
            self.assertEqual([], co.read_text(b"\xff\xd8fake"))
            self.assertEqual({}, co.read_card(b"\xff\xd8fake"))

    def test_engine_failure_on_a_frame_is_swallowed(self):
        broken = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(co, "_name_available", return_value="rapidocr"), \
             mock.patch.object(co, "_build_engine", return_value=("rapidocr", broken)):
            co.reset_engine()
            self.assertTrue(co.available())
            self.assertEqual([], co.read_text(b"\xff\xd8jpeg"))

    def test_reads_text_through_the_engine(self):
        output = mock.Mock()
        output.txts = ("149", "TOTAL 164", "VIEW 103")
        engine = mock.Mock(return_value=output)
        with mock.patch.object(co, "_build_engine", return_value=("rapidocr", engine)):
            co.reset_engine()
            card = co.read_card(b"\xff\xd8jpeg")
        self.assertEqual("149", card["本周"])
        self.assertEqual("164", card["得点"])
        self.assertEqual("103", card["再生"])

    def test_japan_model_failure_falls_back_to_the_default_model(self):
        """打包版里日文模型没进来时，仍要用默认模型读数字（用户 2026-11-18 打的包）。"""
        calls = []

        class FakeRapidOCR:
            def __init__(self, params=None):
                calls.append(params)
                if params:                            # 带参数 = 日文模型，这里让它失败
                    raise RuntimeError("japan model unavailable")

        with mock.patch.dict("sys.modules", {"rapidocr": mock.Mock(RapidOCR=FakeRapidOCR)}):
            co.reset_engine()
            name, engine = co._build_engine()
        self.assertEqual("rapidocr", name)
        self.assertIsInstance(engine, FakeRapidOCR)
        self.assertEqual([{"Rec.lang_type": "japan"}, None], calls)


class OutputShapeTest(unittest.TestCase):
    """用户 2026-11-19：打包后的 exe 里「119/120 帧读取出错，一张都没读到」。

    根因是引擎返回值的形态没认全（`None` / `(result, elapse)` / `.txts` 三种都有）。
    """

    def tearDown(self):
        co.reset_engine()

    def test_accepts_every_engine_return_shape(self):
        class Output:
            txts = ("149", "TOTAL 164")

        self.assertEqual(["149", "TOTAL 164"], co._texts_of(Output()))
        self.assertEqual(["149", "164"],
                         co._texts_of([[[0, 0], "149", 0.9], [[1, 1], "164", 0.8]]))
        self.assertEqual(["149"],
                         co._texts_of(([[[0, 0], "149", 0.9]], 0.12)))
        self.assertEqual([], co._texts_of(None))
        self.assertEqual([], co._texts_of((None, 0.12)))

    def test_a_none_result_is_not_an_error(self):
        engine = mock.Mock(return_value=None)
        with mock.patch.object(co, "_build_engine", return_value=("rapidocr", engine)):
            co.reset_engine()
            self.assertEqual([], co.read_text(b"\xff\xd8jpeg"))
            self.assertEqual({}, co.read_card(b"\xff\xd8jpeg"))

    def test_repeated_failures_switch_the_engine(self):
        """连败太多次就换下一个引擎，不让整轮白等。"""
        broken = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(co, "_build_engine", return_value=("rapidocr", broken)):
            co.reset_engine()
            for _ in range(co.ENGINE_FAIL_LIMIT):
                co.read_text(b"\xff\xd8jpeg")
        self.assertIn("rapidocr", co._SKIPPED)


if __name__ == "__main__":
    unittest.main()
