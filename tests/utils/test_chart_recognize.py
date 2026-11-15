"""榜单画面识别（见 utils/chart_recognize.py）。"""
import json
import unittest
from unittest import mock

from utils import chart_nico, chart_recognize as cr

SPEC = chart_nico.CHARTS["utau"]


class ParseReplyTest(unittest.TestCase):
    def test_rows_object(self):
        rows = cr.parse_reply('{"rows": [{"本周": 1, "曲名": "Lilium", "得点": 12759}]}')
        self.assertEqual([{"本周": "1", "曲名": "Lilium", "得点": "12759"}], rows)

    def test_code_fence_is_tolerated(self):
        text = '```json\n{"rows": [{"rank": 2, "title": "白花"}]}\n```'
        self.assertEqual([{"本周": "2", "曲名": "白花"}], cr.parse_reply(text))

    def test_bare_array_is_tolerated(self):
        self.assertEqual([{"本周": "3", "曲名": "アスタシア"}],
                         cr.parse_reply('[{"順位": 3, "曲名": "アスタシア"}]'))

    def test_extra_prose_is_ignored(self):
        text = '好的，结果如下：{"rows": [{"本周": 1, "曲名": "A"}]} 希望有帮助'
        self.assertEqual([{"本周": "1", "曲名": "A"}], cr.parse_reply(text))

    def test_garbage_is_empty(self):
        self.assertEqual([], cr.parse_reply("看不懂"))
        self.assertEqual([], cr.parse_reply(""))


class NormalizeRowTest(unittest.TestCase):
    def test_aliases_map_to_site_param_names(self):
        row = cr.normalize_row({"順位": 1, "タイトル": "Lilium", "P名": "むのーP",
                                "マイリス": 363, "コメント": 146})
        self.assertEqual({"本周": "1", "曲名": "Lilium", "P主": "むのーP",
                          "评论": "146", "收藏": "363"}, row)

    def test_numbers_lose_grouping_and_units(self):
        self.assertEqual("12759", cr.normalize_row({"曲名": "A", "得点": "12,759"})["得点"])
        self.assertEqual("5358", cr.normalize_row({"曲名": "A", "再生": 5358.0})["再生"])

    def test_null_like_values_are_dropped(self):
        row = cr.normalize_row({"曲名": "A", "得点": None, "点赞": "null", "歌姬": "-"})
        self.assertEqual({"曲名": "A"}, row)

    def test_unknown_keys_are_dropped(self):
        self.assertEqual({"曲名": "A"}, cr.normalize_row({"曲名": "A", "瞎编的": "x"}))

    def test_row_without_rank_or_title_is_dropped(self):
        self.assertEqual({}, cr.normalize_row({"得点": 1234}))
        self.assertEqual({}, cr.normalize_row("不是字典"))


class MergeTest(unittest.TestCase):
    def test_same_rank_merges_and_keeps_the_first_non_empty_value(self):
        rows = cr.merge([{"本周": "1", "曲名": "Lilium"}, {"本周": "1", "得点": "12759"},
                         {"本周": "2", "曲名": "白花"}])
        self.assertEqual([{"本周": "1", "曲名": "Lilium", "得点": "12759"},
                          {"本周": "2", "曲名": "白花"}], rows)

    def test_first_occurrence_wins(self):
        rows = cr.merge([{"本周": "1", "曲名": "先"}, {"本周": "1", "曲名": "后"}])
        self.assertEqual("先", rows[0]["曲名"])

    def test_sort_puts_non_numeric_ranks_first(self):
        rows = cr.sort_rows([{"本周": "3"}, {"本周": "OP"}, {"本周": "1"}])
        self.assertEqual(["OP", "1", "3"], [row["本周"] for row in rows])


class WeightsTest(unittest.TestCase):
    def test_old_rule_derives_the_comment_weight(self):
        """#52 第 1 名：得点 12759 = 5358 + 146×0.97 + 363×20。"""
        row = {"本周": "1", "得点": "12759", "再生": "5358", "评论": "146", "收藏": "363"}
        cr.derive_weights([row])
        self.assertEqual("0.97", row["评论权重"])
        self.assertNotIn("收藏权重", row)

    def test_modern_rule_derives_the_mylist_weight(self):
        """#813 第 1 名：33389 = 24223 + 444×0.99 + (900+273)×7.44。"""
        row = {"本周": "1", "得点": "33389", "再生": "24223", "评论": "444", "评论权重": "0.99",
               "点赞": "900", "收藏": "273"}
        cr.derive_weights([row])
        self.assertEqual("1.00", row["再生权重"])
        self.assertEqual("7.44", row["收藏权重"])
        self.assertEqual("7.44", row["点赞权重"])

    def test_existing_weights_are_left_alone(self):
        row = {"得点": "12759", "再生": "5358", "评论": "146", "收藏": "363", "评论权重": "1.00"}
        cr.derive_weights([row])
        self.assertEqual("1.00", row["评论权重"])

    def test_missing_score_is_skipped(self):
        row = {"再生": "5358", "评论": "146", "收藏": "363"}
        cr.derive_weights([row])
        self.assertNotIn("评论权重", row)


class BatchesTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual([[1, 2], [3]], cr.batches([1, 2, 3], 2))
        self.assertEqual([[1], [2]], cr.batches([1, 2], 0))
        self.assertEqual([], cr.batches([], 4))


class RecognizeTest(unittest.TestCase):
    def frames(self, count=3):
        return [(index / count, b"jpeg" + bytes([index])) for index in range(count)]

    def test_collects_and_merges_batches(self):
        replies = ['{"rows": [{"本周": 1, "曲名": "Lilium", "得点": 12759, "再生": 5358,'
                   ' "评论": 146, "收藏": 363}]}',
                   '{"rows": [{"本周": 2, "曲名": "白花"}]}']
        calls = []

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            calls.append((prompt, len(images)))
            return replies[len(calls) - 1], ""

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               side_effect=lambda data: ("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(4), SPEC, 52, batch=2, progress=None)
        self.assertEqual(2, len(calls))
        self.assertEqual(["1", "2"], [row["本周"] for row in rows])
        self.assertEqual("Lilium", rows[0]["曲名"])

    def test_missing_api_key_raises(self):
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": ""}):
            with self.assertRaises(RuntimeError):
                cr.recognize(self.frames(1), SPEC, 52)

    def test_failed_batch_is_skipped(self):
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=("", "接口挂了")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            self.assertEqual([], cr.recognize(self.frames(2), SPEC, 52, batch=1))

    def test_no_frames_is_empty(self):
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}):
            self.assertEqual([], cr.recognize([], SPEC, 52))

    def test_prompt_names_the_issue_and_the_extra_hint(self):
        prompt = cr.build_prompt(SPEC, 52, 4, 0.25, 0.5, extra="只认前 20 名")
        self.assertIn("周刊UTAU排行榜 #52", prompt)
        self.assertIn("第25%–50%", prompt.replace("-", "–"))
        self.assertIn("只认前 20 名", prompt)

    def test_progress_is_reported(self):
        seen = []
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(json.dumps({"rows": []}), "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            cr.recognize(self.frames(2), SPEC, 52, batch=1,
                         progress=lambda done, total: seen.append((done, total)))
        self.assertEqual([(0, 2), (1, 2), (2, 2)], seen)


if __name__ == "__main__":
    unittest.main()
