"""榜单画面识别（见 utils/chart_recognize.py）。"""
import json
import re
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

    def test_sort_puts_the_op_row_first_and_ed_before_the_extra_section(self):
        # 用户 2026-11-17：#54 / #55 的顺序是 OP → 主榜 → … → HISTORY → ED → 番外
        rows = cr.sort_rows([{"本周": "1", "曲名": "主榜一"},
                             {"本周": "OP", "曲名": "片头", "角标": "OP"},
                             {"本周": "2", "曲名": "主榜二"},
                             {"本周": "ED", "曲名": "片尾", "角标": "ED"},
                             {"本周": "1", "曲名": "番外一", "小节": "番外"},
                             {"本周": "2", "曲名": "往期二", "角标": "HISTORY"},
                             {"本周": "1", "曲名": "往期一", "角标": "HISTORY"}])
        self.assertEqual(["片头", "主榜一", "主榜二", "往期一", "往期二", "片尾", "番外一"],
                         [row["曲名"] for row in rows])

    def test_topic_rows_keep_the_video_order(self):
        # 用户 2026-11-17：TOPIC 区按视频出现先后（`_序`），不按卡片上的数字
        rows = cr.sort_rows([{"本周": "110", "曲名": "第三", "角标": "TOPIC", "_序": "5"},
                             {"本周": "67", "曲名": "第一", "角标": "TOPIC", "_序": "1"},
                             {"本周": "285", "曲名": "第二", "角标": "TOPIC", "_序": "3"}])
        self.assertEqual(["第一", "第二", "第三"], [row["曲名"] for row in rows])

    def test_pickup_rows_come_before_topic_and_after_the_numbers(self):
        rows = cr.sort_rows([{"本周": "67", "曲名": "TOPIC曲", "角标": "TOPIC", "_序": "1"},
                             {"本周": "83", "曲名": "PICKUP曲", "角标": "PICKUP"},
                             {"本周": "20", "曲名": "主榜20"}])
        self.assertEqual(["主榜20", "PICKUP曲", "TOPIC曲"], [row["曲名"] for row in rows])

    def test_badge_rows_without_a_rank_come_last(self):
        rows = cr.sort_rows([{"曲名": "朝食", "角标": "PICKUP"},
                             {"本周": "21", "曲名": "かえりみち", "角标": "PICKUP"},
                             {"本周": "1", "曲名": "一位"}])
        self.assertEqual(["一位", "かえりみち", "朝食"], [row["曲名"] for row in rows])

    def test_last_rank_is_normalised(self):
        # 用户 2026-11-17：`上期：new!!` 不该写成 `|上周 = new!!`（模板自己渲染彩色 NEW!!）
        self.assertEqual("", cr.clean_last_rank("new!!"))
        self.assertEqual("", cr.clean_last_rank("NEW"))
        self.assertEqual("", cr.clean_last_rank("--"))
        self.assertEqual("", cr.clean_last_rank("圏外"))
        self.assertEqual("32", cr.clean_last_rank("32位"))
        self.assertEqual("9", cr.clean_last_rank(" 9 "))
        self.assertEqual("", cr.clean_last_rank(""))
        row = cr.normalize_row({"本周": 6, "曲名": "Lilium", "上期": "1位"})
        self.assertEqual("1", row["上周"])


class SectionTest(unittest.TestCase):
    """番外榜 / History（截图 3）：番外的 1～3 名不能和主榜的 1～3 名撞号合并。"""

    def test_section_names_are_normalized(self):
        self.assertEqual("", cr.normalize_section(None))
        self.assertEqual("", cr.normalize_section("主榜"))
        self.assertEqual("番外", cr.normalize_section("UTAU番外榜"))
        # HISTORY / OP / ED 现在用角标表示，小节一律归主榜（顺序由 sort_rows 排）
        self.assertEqual("", cr.normalize_section("今週のHistory"))
        self.assertEqual("HISTORY", cr.normalize_badge("ヒストリー2位"))
        self.assertEqual("OP", cr.normalize_badge("オープニング曲"))
        self.assertEqual("ED", cr.normalize_badge("エンディング曲"))

    def test_badge_names_are_normalized(self):
        self.assertEqual("PICKUP", cr.normalize_badge("p i c k u p"))
        self.assertEqual("TOPIC", cr.normalize_badge("T O P I C"))
        self.assertEqual("上周冠军", cr.normalize_badge("上周冠军"))
        self.assertEqual("", cr.normalize_badge("#FF9999"))   # 绝不从颜色反推名字

    def test_merge_keys_on_section_and_rank(self):
        rows = cr.merge([{"本周": "1", "曲名": "主榜第一"},
                         {"本周": "1", "曲名": "番外第一", "小节": "番外"},
                         {"本周": "1", "曲名": "番外第一", "小节": "番外", "得点": "8016"}])
        self.assertEqual(["主榜第一", "番外第一"], [row["曲名"] for row in rows])
        self.assertEqual("8016", rows[1]["得点"])

    def test_different_songs_never_merge_even_on_a_rank_clash(self):
        """用户 2026-11-19：番外 1～10 被主榜 1～10 吞掉（模型把番外的小节标成主榜）。"""
        rows = cr.merge([{"本周": "1", "曲名": "主榜第一"},
                         {"本周": "1", "曲名": "番外第一"},
                         {"本周": "2", "曲名": "番外第二"},
                         {"本周": "2", "曲名": "主榜第二"}])
        self.assertEqual(["主榜第一", "番外第一", "番外第二", "主榜第二"],
                         [row["曲名"] for row in rows])

    def test_untitled_rows_merge_into_their_song(self):
        """一批读到曲名、另一批只读到名次 → 仍然是同一首。"""
        rows = cr.merge([{"本周": "1", "曲名": "Lilium"},
                         {"本周": "1", "得点": "12759"},
                         {"本周": "1", "曲名": "Lilium", "再生": "5358"}])
        self.assertEqual(1, len(rows))
        self.assertEqual({"本周": "1", "曲名": "Lilium", "得点": "12759", "再生": "5358"},
                         rows[0])

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

    def test_history_rows_are_kept(self):
        # 用户 2026-11-17：HISTORY 也要写进条目（#54 里就是带 H I S T O R Y 角标的行）
        rows = [{"本周": "1", "曲名": "榜单"},
                {"本周": "1", "曲名": "往期", "角标": "HISTORY"}]
        self.assertEqual(["榜单", "往期"], [row["曲名"] for row in cr.drop_non_chart(rows)])

    def test_only_the_new_source_section_is_dropped(self):
        """「新音源」原始段仍不算榜单；OP / ED 现在要留（#54 / #55 的写法）。"""
        rows = [{"本周": "1", "曲名": "榜单"},
                {"本周": "110", "曲名": "新音源曲", "小节": "新音源"},
                {"本周": "OP", "曲名": "片头", "角标": "OP"}]
        self.assertEqual(["榜单", "片头"], [row["曲名"] for row in cr.drop_non_chart(rows)])


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
        # HISTORY / OP / ED 改用角标表示（用户 2026-11-17），不再是小节
        self.assertEqual({"badge": "HISTORY", "rank": "2"}, cr.parse_label("ヒストリー2位"))
        self.assertEqual({"badge": "OP"}, cr.parse_label("オープニング曲"))
        self.assertEqual({"badge": "ED"}, cr.parse_label("エンディング曲"))
        self.assertNotIn("section", cr.parse_label("ヒストリー2位"))

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


class BorrowRanksTest(unittest.TestCase):
    """用户 2026-11-19：PICKUP / TOPIC 曲的名次本来就在末尾那 89 行里，别先丢掉。"""

    def rows(self):
        return [{"本周": "1", "曲名": "主榜第一", "得点": "4660"},
                {"曲名": "TOPIC候选", "小节": "新音源"},                    # 缺名次
                {"本周": "85", "曲名": "TOPIC候选", "得点": "326", "再生": "219"},  # 整页表那行
                {"曲名": "PICKUP曲"},
                {"本周": "23", "曲名": "PICKUP曲", "得点": "1202", "角标": "PICKUP"}]

    def test_ranks_are_borrowed_before_dropping(self):
        rows = self.rows()
        borrowed = cr._borrow_ranks(rows, 20)
        self.assertEqual(2, borrowed)
        candidate = [row for row in rows if row.get("小节") == "新音源"][0]
        self.assertEqual("85", candidate["本周"])
        self.assertEqual("326", candidate["得点"])
        self.assertEqual("TOPIC", candidate["角标"])       # 20 名之外 + 没角标 → TOPIC
        pickup = [row for row in rows if row["曲名"] == "PICKUP曲"][0]
        self.assertEqual("23", pickup["本周"])
        self.assertEqual("PICKUP", pickup["角标"])          # 已有角标就不动

    def test_rows_that_already_have_a_rank_are_untouched(self):
        rows = [{"本周": "1", "曲名": "A"}, {"本周": "2", "曲名": "A", "得点": "9"}]
        self.assertEqual(0, cr._borrow_ranks(rows, 20))
        self.assertEqual("1", rows[0]["本周"])

    def test_history_and_extra_rows_are_never_lent_to(self):
        """用户 2026-11-19 的 #61：HISTORY 第 5 名（炉心融解）被借成了主榜 218 位。"""
        rows = [{"本周": "218", "曲名": "炉心融解", "得点": "68"},
                {"曲名": "炉心融解", "角标": "HISTORY"},
                {"本周": "4", "曲名": "ひらひら", "得点": "2728", "小节": "番外"},
                {"曲名": "ひらひら", "小节": "番外"}]
        self.assertEqual(0, cr._borrow_ranks(rows, 20))
        self.assertEqual("", rows[1].get("本周") or "")
        self.assertEqual("", rows[3].get("本周") or "")

    def test_playlist_sections_are_respected(self):
        """给了播放列表时，属于 HISTORY / 番外 的曲名也不借。"""
        items = [{"video_id": "sm1", "title": "炉心融解", "section": "history", "badge": "HISTORY",
                  "rank": None, "raw_title": "炉心融解", "vocalist": "", "candidate": ""}]
        rows = [{"本周": "218", "曲名": "炉心融解", "得点": "68"},
                {"曲名": "炉心融解", "角标": "HISTORY"}]
        self.assertEqual(0, cr._borrow_ranks(rows, 20, items))

    def test_a_fragment_in_the_tail_table_still_matches(self):
        """整页表把曲名读成片段（`カンタレラ`），包含匹配也要能借回名次。"""
        rows = [{"曲名": "[UTAU]ゆき てくりね - カンタレラ", "角标": "TOPIC"},
                {"本周": "242", "曲名": "カンタレラ", "得点": "242", "再生": "54"}]
        borrowed = cr._borrow_ranks(rows, 20)
        self.assertEqual(1, borrowed)
        self.assertEqual("242", rows[0]["本周"])
        self.assertEqual("242", rows[0]["得点"])


class DedupeRowsTest(unittest.TestCase):
    """用户 2026-11-19：#61 里同一首曲子出现 2～3 次。"""

    def test_duplicates_are_folded_into_the_richest_row(self):
        rows = [{"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024"},
                {"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024",
                 "得点": "1163", "再生": "624"},
                {"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024",
                 "评论": "42"},
                {"本周": "1", "曲名": "别的一首"}]
        merged, folded = cr.dedupe_rows(rows)
        self.assertEqual(2, folded)
        self.assertEqual(2, len(merged))
        first = merged[0]
        self.assertEqual("1163", first["得点"])
        self.assertEqual("42", first["评论"])                # 其他行的非空字段补进来

    def test_same_title_in_both_sections_stays(self):
        rows = [{"本周": "1", "曲名": "同名曲"},
                {"本周": "1", "曲名": "同名曲", "小节": "番外"}]
        merged, folded = cr.dedupe_rows(rows)
        self.assertEqual(0, folded)
        self.assertEqual(2, len(merged))


class OcrItemMatchTest(unittest.TestCase):
    """段信息以播放列表为准：小字读不到也能把番外卡配到番外去（用户 2026-11-19）。"""

    def playlist(self):
        return [{"video_id": "sm1", "raw_title": "主榜第一", "title": "主榜第一",
                 "vocalist": "", "section": "", "rank": 1, "badge": "", "candidate": ""},
                {"video_id": "sm2", "raw_title": "番外第二", "title": "番外第二",
                 "vocalist": "", "section": "番外", "rank": 2, "badge": "", "candidate": ""}]

    def test_title_match_wins_over_the_main_chart(self):
        items = self.playlist()
        by_rank = cr._items_by_rank(items)
        item = cr._ocr_item_for({"本周": "2"}, {"本周": "2"},
                                ["番外第二", "2", "TOTAL", "7156"], items, by_rank, [])
        self.assertEqual("sm2", item["video_id"])

    def test_label_says_extra_and_never_falls_back_to_main(self):
        items = self.playlist()
        by_rank = cr._items_by_rank(items)
        item = cr._ocr_item_for({"本周": "2"}, {"本周": "2"},
                                ["番外ランキング2位", "2"], items, by_rank, [])
        self.assertEqual("sm2", item["video_id"])

    def test_bare_number_still_matches_the_main_chart(self):
        items = self.playlist()
        by_rank = cr._items_by_rank(items)
        item = cr._ocr_item_for({"本周": "1"}, {"本周": "1"}, ["1", "TOTAL", "10"],
                                items, by_rank, [])
        self.assertEqual("sm1", item["video_id"])


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
        self.assertGreaterEqual(len(prompts), 2)
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
        reply = json.dumps({"section": "新音源",
                            "rows": [{"本周": 110, "曲名": "新音源曲"}]})
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(reply, "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1)
        # 没有官方播放列表时「新音源」段不再整批丢掉，而是按 TOPIC 收下（用户 2026-11-19：
        # 第 61 期播放列表 502，20 首新音源全没了，条目里一首 TOPIC 都没有）
        self.assertEqual([("110", "TOPIC")], [(row["本周"], row["角标"]) for row in rows])

    def test_new_sources_are_dropped_when_the_playlist_is_known(self):
        reply = json.dumps({"section": "新音源",
                            "rows": [{"本周": 110, "曲名": "新音源曲"}]})
        playlist = [{"video_id": "sm1", "raw_title": "一首目", "title": "一首目", "vocalist": "",
                     "section": "", "rank": 1, "badge": "", "candidate": ""}]
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", return_value=(reply, "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(1), SPEC, 52, batch=1, playlist=playlist)
        self.assertEqual(["一首目"], [row["曲名"] for row in rows])


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

    def test_new_source_rows_inside_the_window_become_topic(self):
        # 模型把 TOPIC 段的卡片标成「新音源」时，靠画面位置改回 TOPIC 并留在主榜
        rows = [self.row(4, "四位", 20),
                self.row(None, "今もあなたのために", 30, section="新音源"),
                self.row(3, "三位", 40)]
        tagged, count = cr._apply_topic_window(rows)
        self.assertEqual(1, count)
        self.assertEqual("TOPIC", tagged[1]["角标"])
        self.assertEqual("", tagged[1].get("小节", ""))
        self.assertEqual(3, len(cr.drop_non_chart(tagged)), "改回主榜后不会被丢弃")

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


class BackfillTest(unittest.TestCase):
    """第一次就一行没读到的帧再问一遍（用户 2026-11-17：主榜 1～5 名与 TOPIC 段整段丢在空批次里）。"""

    ITEMS = [{"video_id": "sm8753968", "raw_title": "汽車の窓が映す恋",
              "title": "汽車の窓が映す恋", "vocalist": "", "section": "", "rank": 1,
              "badge": "", "candidate": ""},
             {"video_id": "sm8785342", "raw_title": "きるきるにしてあげる",
              "title": "きるきるにしてあげる", "vocalist": "", "section": "", "rank": None,
              "badge": "", "candidate": "TOPIC"}]

    def fake_replies(self, replies):
        calls = []

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            calls.append(prompt)
            if len(calls) <= len(replies):
                return replies[len(calls) - 1], ""
            return '{"rows": []}', ""

        return calls, fake_ask

    def test_missing_numbers_and_topic_cards_are_read_back(self):
        replies = ['{"rows": []}',                       # 第一次
                   '{"rows": []}',                       # 重试
                   '{"rows": [{"曲名": "汽車の窓が映す恋", "得点": 1200, "再生": 800, "上周": null}]}',
                   '{"rows": [{"曲名": "きるきるにしてあげる", "本周": 18, "角标": "TOPIC",'
                   ' "得点": 900, "再生": 400}]}']
        calls, fake_ask = self.fake_replies(replies)
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize([(0.1, b"jpeg"), (0.5, b"jpeg2")], SPEC, 53, batch=1,
                                playlist=self.ITEMS)
        by_title = {row["曲名"]: row for row in rows}
        self.assertEqual("1200", by_title["汽車の窓が映す恋"]["得点"])
        self.assertEqual("1", by_title["汽車の窓が映す恋"]["本周"], "名次仍以播放列表为准")
        self.assertEqual("TOPIC", by_title["きるきるにしてあげる"]["角标"])
        self.assertEqual("18", by_title["きるきるにしてあげる"]["本周"])
        self.assertEqual("sm8785342", by_title["きるきるにしてあげる"]["id"])
        self.assertTrue(any("T O P I C" in prompt for prompt in calls[2:]),
                        "补读的提示词要专门问 TOPIC / PICKUP 卡片")

    def test_colliding_ranks_are_flagged_not_overwritten(self):
        rows = [{"曲名": "0と1が確定する私の可能性", "本周": "18", "id": "sm1"},
                {"曲名": "きるきるにしてあげる", "本周": "18", "角标": "TOPIC", "id": "sm2"}]
        cr._warn_rank_collisions(rows)
        self.assertIn("撞号", rows[0]["备注"])
        self.assertIn("撞号", rows[1]["备注"])

    def test_nothing_missing_means_no_extra_requests(self):
        reply = json.dumps({"rows": [{"本周": 1, "曲名": "汽車の窓が映す恋", "得点": 100,
                                      "再生": 50}]})
        calls, fake_ask = self.fake_replies([reply])
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            cr.recognize([(0.1, b"jpeg")], SPEC, 53, batch=1, playlist=self.ITEMS[:1])
        self.assertEqual(1, len(calls), "没有缺数字的行就不该再问一遍")


class ResampleTest(unittest.TestCase):
    """时间轴空档重采（用户 2026-11-17：主榜 1～5 名、TOPIC 卡片停在没采到的时间点）。

    卡片按名次顺序播，所以「没读到卡片 = 时间轴空档」；空档按 `RESAMPLE_STEP`（0.5 秒）
    重采一遍就有。
    """

    ITEMS = [{"video_id": "sm8753968", "raw_title": "汽車の窓が映す恋",
              "title": "汽車の窓が映す恋", "vocalist": "", "section": "", "rank": 1,
              "badge": "", "candidate": ""},
             {"video_id": "sm8750934", "raw_title": "3拍子5拍子7拍子",
              "title": "3拍子5拍子7拍子", "vocalist": "", "section": "", "rank": 2,
              "badge": "", "candidate": ""}]

    def test_gap_window_is_recomputed_and_rows_come_back(self):
        calls = []

        def fake_refetch(start, end):
            calls.append((start, end))
            return [(start + 0.001, b"jpeg-gap")]

        replies = ['{"rows": []}',                      # 正常那一批：什么也没读到
                   '{"rows": []}',                      # 空批次的宽松重试：还是没读到
                   json.dumps({"rows": [{"本周": 2, "曲名": "3拍子5拍子7拍子",
                                         "得点": 800, "再生": 400}]})]
        answers = iter(replies)

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr, "_ocr_rows", return_value=([], set())), \
             mock.patch.object(cr.ai_css, "ask",
                               side_effect=lambda *a, **k: (next(answers, '{"rows": []}'), "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize([(0.10, b"jpeg")], SPEC, 53, batch=1, playlist=self.ITEMS,
                                duration=1000.0, refetch=fake_refetch)
        self.assertTrue(calls, "没读到名次时应该重采空档")
        by_title = {row["曲名"]: row for row in rows}
        self.assertEqual("800", by_title["3拍子5拍子7拍子"]["得点"], "重采回来的数字要并进表格")
        self.assertEqual("2", by_title["3拍子5拍子7拍子"]["本周"])

    def test_no_refetch_means_no_resampling(self):
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr, "_ocr_rows", return_value=([], set())), \
             mock.patch.object(cr.ai_css, "ask", return_value=('{"rows": []}', "")), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize([(0.10, b"jpeg")], SPEC, 53, batch=1, playlist=self.ITEMS,
                                duration=1000.0)
        self.assertEqual(2, len(rows), "没有 refetch 就只按播放列表补行（两首各一行）")

    def test_gap_windows_skip_covered_frames(self):
        positions = [0.10, 0.20, 0.30]
        covered = {cr._position_key(0.30)}
        windows = cr._gap_windows(positions, covered, 300.0)
        self.assertEqual(1, len(windows), "中间那段连着的空档应该合成一个窗口")
        self.assertLess(windows[0][0], 0.10)
        self.assertGreater(windows[0][1], 0.20)

    def test_long_gaps_are_split(self):
        # 0.1 的窗口在 1000 秒的片子里是 100 秒，超过单窗口上限 → 拆成两个
        windows = cr._gap_windows([0.10, 0.20], set(), 1000.0)
        self.assertEqual(2, len(windows))

    def test_short_gaps_are_ignored(self):
        # 只有一帧没读到、窗口又短于 GAP_SECONDS → 不值得再重采
        self.assertEqual([], cr._gap_windows([0.10], set(), 10.0))

    def test_long_span_is_capped_by_the_total_budget(self):
        """用户 2026-11-18：`102～1202秒` 那种巨型窗口白烧了 10 分钟，总秒数要有上限。"""
        positions = [index / 2000.0 for index in range(0, 2000, 2)]
        windows = cr._gap_windows(positions, set(), 2000.0)
        covered = sum((end - start) * 2000.0 for start, end in windows)
        self.assertLessEqual(covered, cr.RESAMPLE_TOTAL_SECONDS + cr.GAP_MAX_SECONDS)
        self.assertLessEqual(len(windows), cr.RESAMPLE_BUDGET)


class SkeletonRowsTest(unittest.TestCase):
    """识别整段失败时的兜底：只按播放列表摆行（用户 2026-11-18 的 #61）。"""

    ITEMS = [
        {"video_id": "sm1", "title": "オープニング曲", "raw_title": "オープニング曲",
         "section": "", "rank": "OP", "badge": "OP", "candidate": ""},
        {"video_id": "sm2", "title": "一首目", "raw_title": "一首目", "section": "",
         "rank": 1, "badge": "", "candidate": ""},
        {"video_id": "sm3", "title": "エンディング曲", "raw_title": "エンディング曲",
         "section": "", "rank": "ED", "badge": "ED", "candidate": ""},
        {"video_id": "sm4", "title": "番外的一首", "raw_title": "番外的一首",
         "section": "番外", "rank": 1, "badge": "", "candidate": ""},
    ]

    def test_rows_follow_the_playlist_without_numbers(self):
        rows = cr.skeleton_rows(self.ITEMS, 20)
        self.assertEqual(["OP", "1", "ED", "1"], [row.get("本周") for row in rows])
        self.assertEqual(["sm1", "sm2", "sm3", "sm4"], [row.get("id") for row in rows])
        self.assertEqual("番外", rows[-1].get("小节"))
        for row in rows:
            self.assertNotIn("得点", row, "没有画面就没有数字")

    def test_no_items_is_empty(self):
        self.assertEqual([], cr.skeleton_rows([], 20))


class OcrSkipTest(unittest.TestCase):
    """本地 OCR 读全的批次不再调 AI（用户 2026-11-17 的建议）。"""

    def test_fully_read_batch_skips_the_model(self):
        frames = [(0.1, b"jpeg1"), (0.2, b"jpeg2")]
        ocr_rows = [{"本周": "1", "曲名": "汽車の窓が映す恋", "得点": "100", "再生": "50",
                     cr.FRAME_KEY: "0.100"},
                    {"本周": "2", "曲名": "3拍子5拍子7拍子", "得点": "90", "再生": "40",
                     cr.FRAME_KEY: "0.200"}]
        covered = {cr._position_key(0.1), cr._position_key(0.2)}
        asked = []
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr, "_ocr_rows", return_value=(ocr_rows, covered)), \
             mock.patch.object(cr.ai_css, "ask",
                               side_effect=lambda *a, **k: asked.append(1) or ('{"rows": []}', "")):
            rows = cr.recognize(frames, SPEC, 53, batch=2)
        self.assertEqual([], asked, "OCR 已经读全了就不该再问模型")
        self.assertEqual({"1", "2"}, {row["本周"] for row in rows})

    def test_only_a_budget_of_frames_is_read(self):
        """一张卡片 ~2 秒，600 帧全读要 20 分钟 —— 必须限量（用户 2026-11-18 实测）。

        只挑 `budget` 帧、而且**均匀铺满时间轴**：头尾都要覆盖到，别只读前 120 帧。
        """
        frames = [(index / 600.0, b"\xff\xd8jpeg") for index in range(600)]
        seen = []

        def fake_read(data):
            seen.append(data)
            return {}, []

        with mock.patch.object(cr.chart_ocr, "available", return_value=True), \
             mock.patch.object(cr.chart_ocr, "read_card_lines", side_effect=fake_read):
            rows, covered = cr._ocr_rows(frames, [], budget=10, deadline=30.0, workers=2)
        self.assertEqual([], rows)
        self.assertEqual(10, len(seen))
        self.assertEqual(600, len(frames), "原始帧列表不能被改动")

    def test_spread_frames_covers_the_whole_timeline(self):
        frames = [(index / 100.0, b"x") for index in range(100)]
        picked = cr._spread_frames(frames, 5)
        self.assertEqual(5, len(picked))
        self.assertEqual(0.0, picked[0][0])
        self.assertGreaterEqual(picked[-1][0], 0.95, "最后一帧要落在片尾附近")

    def test_spread_keeps_everything_when_under_budget(self):
        frames = [(0.1, b"a"), (0.2, b"b")]
        self.assertEqual(frames, cr._spread_frames(frames, 10))

    def test_a_read_batch_covers_all_of_its_frames(self):
        """用户 2026-11-18：只记「每批第一帧」害得 3/4 的帧被当成没读到 → 巨型空档。

        一批读到行，这一批的**每一帧**都算看过，不该再触发重采。
        """
        frames = [(index / 10.0, b"jpeg") for index in range(10)]
        asked = []

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            asked.append(1)
            match = re.search(r"取自全片第(\d+)%", prompt)
            rank = int(match.group(1)) if match else 1
            return json.dumps({"rows": [{"本周": rank, "曲名": f"曲{rank}"}]}), ""

        refetched = []

        def refetch(start, end):
            refetched.append((start, end))
            return []

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")), \
             mock.patch.object(cr, "_ocr_rows", return_value=([], set())):
            cr.recognize(frames, SPEC, 52, batch=2, parallel=1, duration=100.0,
                         refetch=refetch)
        self.assertEqual([], refetched, "整批都读到行，就没有空档可重采")

    def test_ocr_logs_what_it_did(self):
        """用户 2026-11-18：主 pass 里 OCR 2 秒返回 0 行却没留下任何线索。"""
        frames = [(0.1, b"\xff\xd8jpeg"), (0.2, b"\xff\xd8jpeg")]

        def fake_read(data):
            raise RuntimeError("模型没进包")

        with mock.patch.object(cr.chart_ocr, "available", return_value=True), \
             mock.patch.object(cr.chart_ocr, "read_card_lines", side_effect=fake_read):
            with self.assertLogs(level="WARNING") as logs:
                cr._ocr_rows(frames, [], budget=4, deadline=10.0, workers=1)
        joined = "\n".join(logs.output)
        self.assertIn("本地OCR", joined)
        self.assertIn("模型没进包", joined)

    def test_ocr_says_why_it_did_nothing(self):
        """引擎不可用 / 没有图片时以前是静默返回，日志里看不出「为什么是 0 张」。"""
        frames = [(0.1, b"\xff\xd8jpeg")]
        with mock.patch.object(cr.chart_ocr, "available", return_value=False), \
             mock.patch.object(cr.chart_ocr, "engine", return_value=""):
            with self.assertLogs(level="WARNING") as logs:
                rows, covered = cr._ocr_rows(frames, [], budget=4, deadline=10.0, workers=1)
        self.assertEqual(([], set()), (rows, covered))
        self.assertIn("本地OCR不可用", "\n".join(logs.output))
        with self.assertLogs(level="WARNING") as logs:
            cr._ocr_rows([(0.1, b"not-an-image")], [], budget=4, deadline=10.0, workers=1)
        self.assertIn("没有可读的图片", "\n".join(logs.output))


class ParallelBatchesTest(unittest.TestCase):
    """`ai_parallel > 1` 时结果必须与串行完全一致（用户 2026-11-18 的提速要求）。

    每批都是互不依赖的请求，串行跑 157 批就是 7 分钟；并行只改「什么时候问」，
    不许改「问出什么」——所以这里直接比较串行与并行的行内容。
    """

    def frames(self, count=6):
        return [(index / count, b"jpeg" + bytes([index])) for index in range(count)]

    def run_with(self, parallel: int):
        """按「这一批第一帧的位置」还原成名次，回一句固定内容（与线程调度顺序无关）。"""
        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            match = re.search(r"取自全片第(\d+)%", prompt)
            rank = int(match.group(1)) * 6 // 100 + 1 if match else 1
            return json.dumps({"rows": [{"本周": rank, "曲名": f"曲{rank}"}]}), ""

        progress = []
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(6), SPEC, 52, batch=1, parallel=parallel,
                                progress=lambda done, total: progress.append((done, total)))
        return rows, progress

    def test_parallel_matches_serial(self):
        serial, _serial_progress = self.run_with(1)
        parallel, _parallel_progress = self.run_with(4)
        self.assertEqual([row["本周"] for row in serial],
                         [row["本周"] for row in parallel])
        self.assertEqual([row["曲名"] for row in serial], [row["曲名"] for row in parallel])

    def test_progress_finishes_at_the_total(self):
        _rows, progress = self.run_with(4)
        self.assertEqual((6, 6), progress[-1])
        done = [entry[0] for entry in progress]
        self.assertEqual(sorted(done), done, "进度不能往回跳")

    def test_badge_pass_gives_up_after_fruitless_tries(self):
        """#61 实测：29 批补问底色、确认 0 行 —— 连白 5 次就该停（用户 2026-11-18）。"""
        asked = {"badge": 0}

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            if "底色" in prompt:
                asked["badge"] += 1
                return '{"badges": {}}', ""
            match = re.search(r"取自全片第(\d+)%", prompt)
            rank = int(match.group(1)) * 6 // 100 + 30 if match else 30
            return json.dumps({"rows": [{"本周": rank, "曲名": f"曲{rank}"}]}), ""

        frames = [(index / 20.0, b"jpeg") for index in range(20)]
        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")), \
             mock.patch.object(cr, "_ocr_rows", return_value=([], set())):
            cr.recognize(frames, SPEC, 52, batch=1, parallel=1, main_limit=20)
        self.assertEqual(cr.BADGE_GIVE_UP_AFTER, asked["badge"])

    def test_one_failed_batch_does_not_stop_the_others(self):
        """按帧位置决定回什么（并行下调用顺序不定，不能再靠计数器）。"""

        def fake_ask(cfg, prompt, images=(), system=None, max_tokens=0, temperature=0.0):
            match = re.search(r"取自全片第(\d+)%", prompt)
            index = int(match.group(1)) if match else 0
            if index == 33:                       # 中间那一批固定失败（含它的重试）
                return "", "接口挂了"
            return json.dumps({"rows": [{"本周": index, "曲名": f"曲{index}"}]}), ""

        with mock.patch.object(cr.ai_css, "settings", return_value={"api_key": "k"}), \
             mock.patch.object(cr.ai_css, "ask", side_effect=fake_ask), \
             mock.patch.object(cr.ai_css, "encode_image_bytes",
                               return_value=("image/jpeg", "b64")):
            rows = cr.recognize(self.frames(3), SPEC, 52, batch=1, parallel=3)
        self.assertEqual(["曲0", "曲66"], sorted(row["曲名"] for row in rows))


if __name__ == "__main__":
    unittest.main()
