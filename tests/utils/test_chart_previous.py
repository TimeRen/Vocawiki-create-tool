# -*- coding: utf-8 -*-
"""上一期条目 → 这一期的「上周」名次（见 utils/chart_previous.py）。"""
import unittest

from utils import chart_nico, chart_previous as cp

SPEC = chart_nico.CHARTS["utau"]

# 取自站上《周刊UTAU排行榜52》的真实片段（`|id` 是纯数字，没有 sm/nm 前缀）
WIKITEXT = """
{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8731570
|曲名 = そう言えば、忘れてた
|本周 = 22
}}
{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8736774
|曲名 = UTAU三人娘でGO MY WAY!! 改
|小节 = 番外
|本周 = 6
}}
{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8726609
|曲名 = カルシウムガール
|小节 = 番外
|本周 = 1
}}
{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8733626
|曲名 = オービタル 手描きPV
|小节 = 番外
|本周 = 5
}}
"""


class Row:
    """够用的假 ChartRow（只用到 video_id / last / section）。"""

    def __init__(self, video_id, last="", section=""):
        self.video_id = video_id
        self.last = last
        self.section = section


class ParseRanksTest(unittest.TestCase):
    def test_parses_ids_and_ranks(self):
        table = cp.parse_ranks(WIKITEXT)
        self.assertEqual("22", table["8731570"][""])
        self.assertEqual("6", table["8736774"]["番外"])

    def test_ignores_blocks_without_an_id(self):
        self.assertEqual({}, cp.parse_ranks("{{VOCALOID_&_UTAU_Ranking/bricks\n|本周 = 3\n}}"))


class ApplyLastRankTest(unittest.TestCase):
    def test_fills_the_three_hantai_rows(self):
        # 用户 2026-11-17：番外 1、2、4 名在 52 期登场过，不该写成 new
        rows = [Row("sm8736774", section="番外"), Row("sm8726609", section="番外"),
                Row("sm8733626", section="番外")]
        filled = cp.apply_last_rank(rows, cp.parse_ranks(WIKITEXT))
        self.assertEqual(3, filled)
        self.assertEqual(["6", "1", "5"], [row.last for row in rows])

    def test_keeps_a_rank_read_from_the_picture(self):
        rows = [Row("sm8736774", last="3", section="番外")]
        self.assertEqual(0, cp.apply_last_rank(rows, cp.parse_ranks(WIKITEXT)))
        self.assertEqual("3", rows[0].last)

    def test_main_rows_use_the_main_rank(self):
        rows = [Row("sm8731570")]
        self.assertEqual(1, cp.apply_last_rank(rows, cp.parse_ranks(WIKITEXT)))
        self.assertEqual("22", rows[0].last)

    def test_unknown_songs_stay_empty(self):
        rows = [Row("sm8757272")]
        self.assertEqual(0, cp.apply_last_rank(rows, cp.parse_ranks(WIKITEXT)))
        self.assertEqual("", rows[0].last)

    def test_empty_table_is_a_no_op(self):
        rows = [Row("sm8736774")]
        self.assertEqual(0, cp.apply_last_rank(rows, {}))
        self.assertEqual("", rows[0].last)


class IssueTitleTest(unittest.TestCase):
    def test_reads_the_previous_issue(self):
        self.assertIn("52", cp.issue_title(SPEC, 53))

    def test_first_issue_has_no_previous(self):
        self.assertEqual("", cp.issue_title(SPEC, 1))


class PreviousRanksTest(unittest.TestCase):
    def tearDown(self):
        cp.forget()

    def test_uses_the_injected_fetcher_and_caches_it(self):
        calls = []

        def fetch(title):
            calls.append(title)
            return WIKITEXT

        cp.forget()
        first = cp.previous_ranks(SPEC, 53, fetch=fetch)
        second = cp.previous_ranks(SPEC, 53, fetch=fetch)
        self.assertEqual("6", first["8736774"]["番外"])
        self.assertEqual(first, second)
        self.assertEqual(1, len(calls), "同一次运行里上一期只读一遍")

    def test_fetch_failure_returns_an_empty_table(self):
        def boom(_title):
            raise RuntimeError("网络断了")

        cp.forget()
        self.assertEqual({}, cp.previous_ranks(SPEC, 53, fetch=boom))


if __name__ == "__main__":
    unittest.main()
