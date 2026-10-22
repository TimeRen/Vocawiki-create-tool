"""APK「歌词」页的「比对中日歌词」：逐行配对 / 还原（`mobile/lyrics_compare.py`）。

用户 2026-10-23 要求：手机上两栏没法并排看，想在界面里逐行比对并修改中日歌词。
配对逻辑是纯字符串处理，这里直接测，不需要 Kivy。
"""
from unittest import TestCase

from mobile import lyrics_compare


class PairLinesTest(TestCase):
    def test_pairs_line_by_line_and_keeps_paragraph_gaps(self):
        rows = lyrics_compare.pair_lines("きみの\nぼくの\n\nあいの", "你的\n我的\n\n爱的")
        self.assertEqual(["line", "line", "gap", "line"],
                         [row["kind"] for row in rows])
        self.assertEqual(["きみの", "ぼくの", "あいの"],
                         [row["jap"] for row in rows if row["kind"] == "line"])
        self.assertEqual(["你的", "我的", "爱的"],
                         [row["chs"] for row in rows if row["kind"] == "line"])
        self.assertEqual([1, 2, 3], [row["no"] for row in rows if row["kind"] == "line"])

    def test_a_missing_translation_shows_up_as_one_empty_row(self):
        """译文少一句时不会丢行：空出来的译文字段留空给人补（哪一句缺只能人来判断）。

        按**绝对行号**配会让少一句之后的每一行都错位；这里按「非空行顺序」配，
        日语行一句都不少、顺序也不变，只是有一行的译文字段是空的。
        """
        rows = lyrics_compare.pair_lines("きみの\nぼくの\nあいの", "你的\n爱的")
        lines = [row for row in rows if row["kind"] == "line"]
        self.assertEqual(["きみの", "ぼくの", "あいの"], [row["jap"] for row in lines])
        self.assertEqual(1, len([row for row in lines if not row["chs"]]))
        self.assertEqual(1, lyrics_compare.row_problems(rows)["missing_chs"])

    def test_extra_translation_lines_are_shown_too(self):
        rows = lyrics_compare.pair_lines("きみの", "你的\n多的一句")
        lines = [row for row in rows if row["kind"] == "line"]
        self.assertEqual(2, len(lines))
        self.assertEqual(("", "多的一句"), (lines[1]["jap"], lines[1]["chs"]))

    def test_blank_lines_at_the_edges_are_dropped(self):
        rows = lyrics_compare.pair_lines("\n\nきみの\n\n", "\n你的\n\n\n")
        self.assertEqual(1, len(rows))
        self.assertEqual("きみの", rows[0]["jap"])

    def test_empty_columns(self):
        self.assertEqual([], lyrics_compare.pair_lines("", ""))
        rows = lyrics_compare.pair_lines("きみの\nぼくの", "")
        self.assertEqual(["", ""], [row["chs"] for row in rows])


class JoinLinesTest(TestCase):
    def test_round_trip_restores_both_columns(self):
        jap, chs = "きみの\nぼくの\n\nあいの", "你的\n我的\n\n爱的"
        self.assertEqual((jap, chs), lyrics_compare.join_lines(
            lyrics_compare.pair_lines(jap, chs)))

    def test_edits_and_deletions_are_kept(self):
        rows = lyrics_compare.pair_lines("きみの\nぼくの\nあいの", "你的\n我的\n爱的")
        lines = [row for row in rows if row["kind"] == "line"]
        lines[1]["chs"] = "我的（改过）"
        rows.remove(lines[2])                       # 删掉第三行
        jap, chs = lyrics_compare.join_lines(rows)
        self.assertEqual("きみの\nぼくの", jap)
        self.assertEqual("你的\n我的（改过）", chs)

    def test_trailing_blank_lines_are_dropped(self):
        rows = [{"kind": "line", "no": 1, "jap": "きみの", "chs": "你的"},
                {"kind": "gap"}]
        self.assertEqual(("きみの", "你的"), lyrics_compare.join_lines(rows))


class ProblemsTest(TestCase):
    def test_missing_translation_is_counted(self):
        rows = lyrics_compare.pair_lines("きみの\nぼくの", "你的")
        self.assertEqual(1, lyrics_compare.row_problems(rows)["missing_chs"])
        self.assertIn("1 行没有译文", lyrics_compare.summary(rows))

    def test_japanese_left_in_the_chinese_column_is_flagged(self):
        """译文里还留着假名说明这一行没译完（《若要讨伐那条龙的话》那类）。"""
        rows = lyrics_compare.pair_lines("きみの\nぼくの", "你的\nぼくのまま")
        self.assertEqual(1, lyrics_compare.row_problems(rows)["japanese_in_chs"])
        self.assertTrue(lyrics_compare.first_problem(
            [row for row in rows if row["kind"] == "line"][1]))

    def test_kana_punctuation_is_not_treated_as_japanese(self):
        """`・・・`（片假名区的间隔号）不算假名：中文里常这么写，不该被标成没译完。"""
        rows = lyrics_compare.pair_lines("きみの", "「我才沒有・・・討厭你喔？」")
        self.assertEqual(0, lyrics_compare.row_problems(rows)["japanese_in_chs"])
        self.assertIsNone(lyrics_compare.first_problem(rows[0]))

    def test_a_clean_pairing_has_no_problems(self):
        rows = lyrics_compare.pair_lines("きみの\n\nぼくの", "你的\n\n我的")
        self.assertEqual({"missing_chs": 0, "missing_jap": 0, "japanese_in_chs": 0},
                         lyrics_compare.row_problems(rows))
        self.assertEqual("2 行、1 个分段", lyrics_compare.summary(rows))
