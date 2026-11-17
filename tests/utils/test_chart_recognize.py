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


class SectionTest(unittest.TestCase):
    """番外榜 / History（截图 3）：番外的 1～3 名不能和主榜的 1～3 名撞号合并。"""

    def test_section_names_are_normalized(self):
        self.assertEqual("", cr.normalize_section(None))
        self.assertEqual("", cr.normalize_section("主榜"))
        self.assertEqual("番外", cr.normalize_section("UTAU番外榜"))
        self.assertEqual("history", cr.normalize_section("今週のHistory"))

    def test_badge_names_are_normalized(self):
        self.assertEqual("PICKUP", cr.normalize_badge("p i c k u p"))
        self.assertEqual("TOPIC", cr.normalize_badge("T O P I C"))
        self.assertEqual("上周冠军", cr.normalize_badge("上周冠军"))
        self.assertEqual("", cr.normalize_badge("#FF9999"))   # 绝不从颜色反推名字

    def test_merge_keys_on_section_and_rank(self):
        rows = cr.merge([{"本周": "1", "曲名": "主榜第一"},
                         {"本周": "1", "曲名": "番外第一", "小节": "番外"},
                         {"本周": "1", "曲名": "番外第一（重复）", "小节": "番外"}])
        self.assertEqual(["主榜第一", "番外第一"], [row["曲名"] for row in rows])

    def test_sort_puts_the_extra_section_after_the_main_chart(self):
        rows = [{"本周": "1", "小节": "番外"}, {"本周": "2"}, {"本周": "1"}]
        self.assertEqual([("", "1"), ("", "2"), ("番外", "1")],
                         [(row.get("小节", ""), row["本周"]) for row in cr.sort_rows(rows)])

    def test_missing_ranks_are_reported_per_section(self):
        rows = [{"本周": "1"}, {"本周": "2"}, {"本周": "4"},
                {"本周": "1", "小节": "番外"}]
        self.assertEqual({"": [3]}, cr.missing_ranks(rows))

    def test_contiguous_ranks_have_no_gaps(self):
        self.assertEqual({}, cr.missing_ranks([{"本周": "1"}, {"本周": "2"}]))

    def test_history_rows_are_dropped(self):
        rows = [{"本周": "1", "曲名": "榜单"},
                {"本周": "1", "曲名": "往期", "小节": "history"}]
        self.assertEqual(["榜单"], [row["曲名"] for row in cr.drop_non_chart(rows)])

    def test_new_source_and_op_ed_are_dropped_too(self):
        """「新音源」「オープニング曲 / エンディング曲」也不是榜单。"""
        rows = [{"本周": "1", "曲名": "榜单"},
                {"本周": "110", "曲名": "新音源曲", "小节": "新音源"},
                {"本周": "1", "曲名": "片头曲", "小节": "op-ed"}]
        self.assertEqual(["榜单"], [row["曲名"] for row in cr.drop_non_chart(rows)])


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


class LabelTest(unittest.TestCase):
    """卡片上那行小字（`番外ランキング3位` / `ピックアップ曲` / `新音源`）→ 小节 / 角标 / 名次。"""

    def test_extra_rank_comes_from_the_label(self):
        self.assertEqual({"section": "番外", "rank": "3"},
                         cr.parse_label("番外ランキング3位"))
        self.assertEqual({"section": "番外", "rank": "1"}, cr.parse_label("UTAU番外榜1位"))

    def test_pickup_and_topic_come_from_words_not_colours(self):
        self.assertEqual({"badge": "PICKUP"}, cr.parse_label("ピックアップ曲"))
        self.assertEqual({"badge": "TOPIC"}, cr.parse_label("T O P I C"))
        self.assertEqual({}, cr.parse_label("#FF9999"))

    def test_non_chart_sections(self):
        self.assertEqual("新音源", cr.parse_label("新音源")["section"])
        self.assertEqual("history", cr.parse_label("ヒストリー2位")["section"])
        self.assertEqual("op-ed", cr.parse_label("オープニング曲")["section"])

    def test_plain_rank_label(self):
        self.assertEqual({"rank": "7"}, cr.parse_label("7位"))
        self.assertEqual({}, cr.parse_label(""))
        self.assertEqual({}, cr.parse_label(None))

    def test_normalize_row_applies_the_label(self):
        row = cr.normalize_row({"标签": "番外ランキング3位", "本周": 3,
                                "曲名": "カルシウムガール", "得点": 2805})
        self.assertEqual("番外", row["小节"])
        self.assertEqual("3", row["本周"])

    def test_pickup_label_sets_the_badge(self):
        row = cr.normalize_row({"标签": "ピックアップ曲", "本周": 26, "曲名": "六花待ち"})
        self.assertEqual("PICKUP", row["角标"])
        self.assertEqual("", row.get("小节", ""))

    def test_new_source_rows_are_marked_and_dropped(self):
        row = cr.normalize_row({"标签": "新音源", "本周": 110, "曲名": "右肩の蝶"})
        self.assertEqual("新音源", row["小节"])
        self.assertEqual([], cr.drop_non_chart([row]))

    def test_extra_rows_no_longer_collide_with_main_ranks(self):
        """#53 的真事：番外 1～5 名被当成主榜 1～5 名，主榜第 1 名整首丢失。"""
        rows = cr.merge([{"本周": "1", "曲名": "汽車の窓が映す恋"},
                         {"本周": "1", "曲名": "UTAU三人娘でGO MY WAY!!", "小节": "番外"}])
        self.assertEqual(["汽車の窓が映す恋", "UTAU三人娘でGO MY WAY!!"],
                         [row["曲名"] for row in cr.sort_rows(rows)])


class TrimMainTest(unittest.TestCase):
    """用户 2026-11-16 的口径：主榜只收录前 20 名，20 名之后除非是 PICKUP / TOPIC 否则不收。"""

    def rows(self):
        rows = [{"本周": str(index), "曲名": f"曲{index}"} for index in range(1, 41)]
        rows.append({"本周": "26", "曲名": "六花待ち", "角标": "PICKUP"})
        rows.append({"本周": "88", "曲名": "TOPIC曲", "角标": "TOPIC"})
        rows.append({"本周": "3", "曲名": "番外第三", "小节": "番外"})
        return rows

    def test_drops_tail_but_keeps_pickup_topic_and_extra(self):
        kept, dropped = cr.trim_main(self.rows(), 20)
        titles = [row["曲名"] for row in kept]
        self.assertEqual(20, dropped)                   # 21～40 共 20 首被丢掉
        self.assertIn("曲20", titles)
        self.assertNotIn("曲21", titles)
        self.assertIn("六花待ち", titles)
        self.assertIn("TOPIC曲", titles)
        self.assertIn("番外第三", titles)

    def test_zero_limit_keeps_everything(self):
        rows = self.rows()
        self.assertEqual((rows, 0), cr.trim_main(rows, 0))

    def test_missing_ranks_only_checks_the_limit(self):
        rows = [{"本周": str(index), "曲名": "x"} for index in range(6, 31)]
        self.assertEqual({"": [1, 2, 3, 4, 5]}, cr.missing_ranks(rows, limit=20))
        self.assertEqual({"": [1, 2, 3, 4, 5]}, cr.expected_gaps(rows, limit=20))

    def test_history_and_new_source_never_count_as_gaps(self):
        rows = [{"本周": "1", "曲名": "a"}, {"本周": "1", "曲名": "h", "小节": "history"},
                {"本周": "110", "曲名": "n", "小节": "新音源"}]
        self.assertEqual({}, cr.missing_ranks(rows, limit=20))


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

    def test_empty_batch_is_retried_with_the_lenient_prompt(self):
        """实测 69 批里 11 批读到 0 行（番外 / PICKUP 卡片被「片头片尾不抄」误伤）。"""
        prompts = []

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            prompts.append(prompt)
            if len(prompts) == 1:
                return '{"rows": []}', ""
            return '{"rows": [{"本周": 2, "曲名": "カルシウムガール", "标签": "番外ランキング2位"}]}', ""

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1)
        self.assertEqual(2, len(prompts))
        self.assertIn("一行都没抄出来", prompts[1])
        self.assertEqual("番外", rows[0]["小节"])
        self.assertEqual("2", rows[0]["本周"])

    def test_tail_rows_are_trimmed_and_pickup_kept(self):
        reply = json.dumps({"rows": [
            {"本周": 1, "曲名": "主榜第一"},
            {"本周": 25, "曲名": "点名曲", "标签": "ピックアップ曲"},
            {"本周": 30, "曲名": "列表里的第30名"},
        ]})

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            return reply, ""

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1, main_limit=20)
        self.assertEqual(["主榜第一", "点名曲"], [row["曲名"] for row in rows])

    def test_badge_second_pass_fills_the_colour_rows(self):
        """角标在抄表时没读到 → 对整批再问一次「哪些行是粉/绿底」。"""
        replies = ['{"rows": [{"本周": 26, "曲名": "六花待ち"}]}',
                   '{"badges": {"26": "PICKUP"}}']
        asked = []

        def ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            asked.append(prompt)
            return replies[min(len(asked) - 1, len(replies) - 1)], ""

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1, main_limit=20)
        self.assertEqual(2, len(asked))
        self.assertIn("底色", asked[1])
        self.assertEqual([("26", "PICKUP")],
                         [(row["本周"], row["角标"]) for row in rows if row.get("角标")])

    def test_top_level_section_covers_the_whole_batch(self):
        reply = json.dumps({"section": "history",
                            "rows": [{"本周": 1, "曲名": "往期曲"}]})
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(reply, "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1)
        self.assertEqual([], rows)          # history 整段丢掉


class TopicWindowTest(unittest.TestCase):
    """段序 `主榜 10→4 ➡ 情报段 ➡ TOPIC段 ➡ 主榜 3→1`：中间的卡片就是 TOPIC。"""

    def row(self, rank=None, title="x", frame=None, section=""):
        row = {"曲名": title}
        if rank is not None:
            row["本周"] = str(rank)
        if section:
            row["小节"] = section
        if frame is not None:
            row[cr.FRAME_KEY] = "%.3f" % frame
        return row

    def test_rows_between_rank4_and_rank3_become_topic(self):
        rows = [self.row(10, "十位", 10), self.row(4, "四位", 20),
                self.row(None, "今もあなたのために", 30),
                self.row(None, "口論", 32),
                self.row(3, "三位", 40), self.row(1, "一位", 50)]
        tagged, count = cr._apply_topic_window(rows)
        self.assertEqual(2, count)
        self.assertEqual(["", "", "TOPIC", "TOPIC", "", ""], [row.get("角标", "") for row in tagged])

    def test_extra_section_rows_are_not_topic(self):
        rows = [self.row(4, "四位", 20), self.row(None, "番外曲", 30, section="番外"),
                self.row(3, "三位", 40)]
        _tagged, count = cr._apply_topic_window(rows)
        self.assertEqual(0, count)

    def test_empty_window_changes_nothing(self):
        rows = [self.row(1, "一位", 10), self.row(None, "某曲", 20)]
        _tagged, count = cr._apply_topic_window(rows)
        self.assertEqual(0, count)

    def test_existing_badge_is_kept(self):
        rows = [self.row(4, "四位", 20), {"曲名": "已是角标", "角标": "PICKUP",
                                          cr.FRAME_KEY: "30.000"}, self.row(3, "三位", 40)]
        tagged, count = cr._apply_topic_window(rows)
        self.assertEqual(0, count)
        self.assertEqual("PICKUP", tagged[1]["角标"])

    def test_frame_marker_is_removed_from_the_result(self):
        reply = json.dumps({"rows": [{"本周": 1, "曲名": "Lilium"}]})
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(reply, "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize([(0.5, b"jpeg")], SPEC, 52, batch=1)
        self.assertEqual([], [key for key in rows[0] if key == cr.FRAME_KEY])


class PlaylistRecognizeTest(unittest.TestCase):
    """有官方播放列表时：行/名次以它为准，画面只贡献数字。"""

    def test_playlist_decides_the_ranks(self):
        reply = json.dumps({"rows": [{"本周": 1, "曲名": "UTAU三人娘でGO MY WAY!! 改",
                                      "得点": 9000}]})
        playlist = [{"video_id": "sm8736774", "raw_title": "UTAU三人娘でGO MY WAY!! 改",
                     "title": "UTAU三人娘でGO MY WAY!! 改", "vocalist": "",
                     "section": "番外", "rank": 1, "badge": "", "candidate": ""}]
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(reply, "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize([(0.5, b"jpeg")], SPEC, 52, batch=1, playlist=playlist)
        self.assertEqual(1, len(rows))
        self.assertEqual("番外", rows[0]["小节"])
        self.assertEqual("1", rows[0]["本周"])
        self.assertEqual("9000", rows[0]["得点"])


if __name__ == "__main__":
    unittest.main()
