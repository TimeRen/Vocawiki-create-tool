"""榜单条目的 wikitext 版式（照站上《周刊UTAU排行榜52》，见 utils/chart_wikitext.py）。"""
import re
import unittest
from unittest import mock

from utils import chart_editor as ce, chart_nico, chart_wikitext as cw

# 站上 #52 的题头 + 导语 + 视频本体 + 三块名次 + 杂谈 + 页脚（实测文本的缩略版：
# 保留了 `nm` 前缀、空 `P主`、`条目` / `翻唱`、PICKUP 的 `color` / `bottom-column` 这些坑）
PAGE_52 = """{{UTAU Ranking
|id = 8763104
|index = 52
|image =
|title-color =
|发布时间 = 2009年11月10日 00:06
|起始时间 = 2009年11月01日 00:00
|终止时间 = 2009年11月08日 00:00
|统计规则 = 2008
}}

'''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''投稿于niconico的UTAU歌曲周刊。

==视频本体==
{{BilibiliVideo|id=BV1GZ4y1x7Zo}}
;视频简介
{{lj|<poem>○集計期間：2009年11月1日0時～2009年11月8日0時

PL:終了 blogにて(http://utauhirohiro.blog56.fc2.com/blog-entry-447.html)</poem>}}
{{clear}}
==榜单==
{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8673630
|曲名 = Lilium
|歌姬 = 重音Teto
|P主 = むのーP
|时间 = 2009-11-01 02:39
|本周 = 1
|得点 = 12759
|再生 = 5358
|评论 = 146
|评论权重 = 0.97
|收藏 = 363
}}

{{VOCALOID_&_UTAU_Ranking/bricks
|前缀 = nm
|id = 8676338
|曲名 = 白花
|歌姬 = 重音Teto、春歌Nana
|P主 =
|时间 = 2009-11-01 09:30
|本周 = 25
|得点 = 1223
|再生 = 423
|评论 = 22
|评论权重 = 0.95
|收藏 = 39
|color = #FF9999
|bottom-column = {{color|#FF9999|P I C K U P}}
}}

{{VOCALOID_&_UTAU_Ranking/bricks
|id = 8683940
|曲名 = 波音リツ連続音でけんか別れ
|条目 = 吵架分手
|翻唱 = 1
|歌姬 = 波音律
|P主 = 名無し<!--不是LamazeP-->
|时间 = 2009-11-01 23:51
|本周 = 2
|得点 = 6019
|再生 = 2239
|评论 = 106
|评论权重 = 0.95
|收藏 = 184
}}

==杂谈==
（待补）

{{周刊UTAU排行榜}}
[[Category:周刊VOCAL Character & UTAU排行榜]]
"""

# #813 的一块（2023 规则的新参数：`image link` / `再生权重` / `点赞` / `四倍阀`）
BRICK_813 = """{{VOCALOID_&_UTAU_Ranking/bricks
|id = 43854078
|曲名 = 优劣弧
|歌姬 = 小感冒、剧药、重音Teto
|P主 = 原口沙辅
|时间 = 2024-05-31 02:49
|image link = https://nicovideo.cdn.nimg.jp/thumbnails/43854078/43854078.54595937.M
|本周 = 1
|上周 = 1
|rate = -40.82%
|得点 = 33389
|再生 = 24223
|再生权重 = 1.00
|评论 = 444
|评论权重 = 0.99
|点赞 = 900
|点赞权重 = 7.44
|收藏 = 273
|收藏权重 = 7.44
|四倍阀 = 1
}}"""


def _norm(text):
    """行尾多余的空格不算差异（站上有 `|P主 = ` 这种）。"""
    return re.sub(r"[ \t]+\n", "\n", text).rstrip("\n")


class BrickTest(unittest.TestCase):
    def test_round_trip_keeps_the_page_bytes(self):
        bricks = cw.parse_bricks(PAGE_52)
        self.assertEqual(3, len(bricks))
        expected = _norm(PAGE_52).split("==榜单==")[1].split("==杂谈==")[0].strip()
        self.assertEqual(expected, _norm("\n\n".join(cw.brick(item) for item in bricks)))

    def test_modern_brick_round_trips(self):
        self.assertEqual(BRICK_813, cw.brick(cw.parse_bricks(BRICK_813)[0]))

    def test_params_keep_the_site_order(self):
        params = {"收藏": "363", "曲名": "Lilium", "id": "8673630", "本周": "1"}
        self.assertEqual("{{VOCALOID_&_UTAU_Ranking/bricks\n|id = 8673630\n|曲名 = Lilium\n"
                         "|本周 = 1\n|收藏 = 363\n}}", cw.brick(params))

    def test_unknown_params_go_last(self):
        text = cw.brick({"id": "1", "曲名": "A", "自定义": "x"})
        self.assertTrue(text.endswith("|自定义 = x\n}}"), text)


class IdTest(unittest.TestCase):
    def test_sm_prefix_is_dropped_from_id(self):
        self.assertEqual("8673630", cw.plain_id("sm8673630"))
        self.assertEqual("", cw.id_prefix("sm8673630"))

    def test_nm_prefix_moves_to_its_own_param(self):
        self.assertEqual("8676338", cw.plain_id("nm8676338"))
        self.assertEqual("nm", cw.id_prefix("nm8676338"))

    def test_bare_digits_pass_through(self):
        self.assertEqual("87820508", cw.plain_id("87820508"))
        self.assertEqual("", cw.id_prefix("87820508"))


class LeadTest(unittest.TestCase):
    def spec(self):
        return chart_nico.CHARTS["utau"]

    def test_old_rule_lead_matches_the_site(self):
        self.assertEqual(
            "'''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''投稿于niconico的UTAU歌曲周刊。",
            cw.lead_sentence(self.spec(), 52, "2009年11月10日 00:06", "あーさん", "2008"))

    def test_modern_rule_lead_drops_the_leading_zai(self):
        self.assertEqual(
            "'''周刊UTAU排行榜 #813'''是2024年8月19日由'''sippotan'''投稿于niconico的UTAU周刊。",
            cw.lead_sentence(self.spec(), 813, "2024年08月19日 03:30", "sippotan", "2023"))

    def test_unknown_uploader_leaves_it_out(self):
        self.assertEqual("'''周刊UTAU排行榜 #5'''是在2009年1月3日投稿于niconico的UTAU歌曲周刊。",
                         cw.lead_sentence(self.spec(), 5, "2009年01月03日 12:00", "", "2008"))


class NavboxTest(unittest.TestCase):
    def test_old_rule_uses_the_utau_navbox(self):
        self.assertEqual("{{周刊UTAU排行榜}}", cw.navbox("2008"))

    def test_modern_rule_uses_the_merged_navbox_with_the_rule_year(self):
        self.assertEqual("{{周刊VOCAL Character & UTAU RANKING|2023}}", cw.navbox("2023规则"))


class PageTest(unittest.TestCase):
    def test_page_round_trip(self):
        """把站上 #52 的题头 / 名次拆出来再拼回去，应当一字不差。"""
        groups = [("", cw.parse_bricks(PAGE_52))]
        page = cw.render_page(cw.parse_header(PAGE_52),
                              "'''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''投稿于niconico的UTAU歌曲周刊。",
                              "○集計期間：2009年11月1日0時～2009年11月8日0時\n\n"
                              "PL:終了 blogにて(http://utauhirohiro.blog56.fc2.com/blog-entry-447.html)",
                              "BV1GZ4y1x7Zo", groups, notes="", rule="2008")
        self.assertEqual(_norm(PAGE_52), _norm(page))

    def test_extra_section_gets_a_blank_line_before_the_heading(self):
        groups = [("", cw.parse_bricks(BRICK_813)[:1]), ("=== UTAU番外榜 ===", [])]
        page = cw.render_page({"id": "1", "index": 2}, "lead", "", "", groups, rule="2008")
        self.assertIn("\n}}\n\n==杂谈==\n（待补）\n\n{{周刊UTAU排行榜}}\n"
                      "[[Category:周刊VOCAL Character & UTAU排行榜]]\n", page)

    def test_missing_bilibili_omits_the_embed_and_clear(self):
        text = cw.video_section("", "○集計期間：…")
        self.assertEqual("==视频本体==\n;视频简介\n{{lj|<poem>○集計期間：…</poem>}}", text)

    def test_empty_description_leaves_out_the_poem(self):
        self.assertEqual("==视频本体==\n{{BilibiliVideo|id=BV1xx}}\n{{clear}}",
                         cw.video_section("BV1xx", ""))

    def duplicate_work(self, rows):
        work = mock.Mock()
        work.video_id, work.index, work.publish, work.start, work.end = "sm1", 61, "", "", ""
        work.rule, work.uploader, work.description = "2008", "", ""
        work.bilibili, work.notes = "", ""
        work.spec = chart_nico.CHARTS["utau"]
        work.rows = [ce.ChartRow.from_recognized(row) for row in rows]
        return work

    def test_duplicate_bricks_are_written_once(self):
        """用户 2026-11-19：同一首曲子被多批读到，条目里出现了 2～3 次。"""
        work = self.duplicate_work([
            {"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024", "得点": "1163"},
            {"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024", "得点": "1163"},
            {"本周": "9", "曲名": "モーションブラーテスト", "id": "sm9302024"}])
        page = cw.render(work)
        self.assertEqual(1, page.count("|曲名 = モーションブラーテスト"))

    def test_extra_section_keeps_its_own_duplicates(self):
        """番外与主榜同名同号是两首不同的曲子，不能被去重吃掉。"""
        work = self.duplicate_work([
            {"本周": "1", "曲名": "同名曲", "id": "sm1"},
            {"本周": "1", "曲名": "同名曲", "id": "sm2", "小节": "番外"}])
        page = cw.render(work)
        self.assertEqual(2, page.count("|曲名 = 同名曲"))
        self.assertIn("=== UTAU番外榜 ===", page)


class BadgeTest(unittest.TestCase):
    """角标 → `|color` + `|bottom-column`（PICKUP 与 TOPIC 绝不能互换）。"""

    def test_pickup_and_topic_use_their_own_colours(self):
        self.assertEqual({"color": "#FF9999",
                          "bottom-column": "{{color|#FF9999|P I C K U P}}"},
                         cw.badge_params("PICKUP"))
        self.assertEqual({"color": "#01BB00",
                          "bottom-column": "{{color|#01BB00|T O P I C}}"},
                         cw.badge_params("TOPIC"))

    def test_last_week_champion_is_red(self):
        self.assertEqual("#AA0000", cw.badge_params("上周冠军")["color"])

    def test_user_color_wins(self):
        self.assertEqual({"bottom-column": "{{color|#FF9999|P I C K U P}}"},
                         cw.badge_params("PICKUP", {"color": "#123456"}))

    def test_unknown_badge_adds_nothing(self):
        self.assertEqual({}, cw.badge_params(""))
        self.assertEqual({}, cw.badge_params("瞎写的"))


class ChartWorkRenderTest(unittest.TestCase):
    """`render(work)` 按小节分组：主榜之后空一行再写 `=== UTAU番外榜 ===`。"""

    def _work(self, rows):
        from utils import chart_editor as ce

        spec = chart_nico.CHARTS["utau"]
        return ce.ChartWork(spec=spec, index=52, video_id="sm8763104", uploader="あーさん",
                            publish="2009年11月10日 00:06", start="2009年11月01日 00:00",
                            end="2009年11月08日 00:00", rule="2008", rows=rows)

    def test_badge_reaches_the_brick(self):
        from utils import chart_editor as ce

        text = cw.render(self._work([ce.ChartRow(rank="1", title="A", video_id="sm1",
                                                 score="100", badge="PICKUP")]))
        self.assertIn("|color = #FF9999", text)
        self.assertIn("|bottom-column = {{color|#FF9999|P I C K U P}}", text)

    def test_history_op_and_ed_reach_the_page(self):
        """用户 2026-11-17：HISTORY / OP / ED 现在都要写进条目（#54 / #55 的写法）。"""
        from utils import chart_editor as ce

        text = cw.render(self._work([
            ce.ChartRow(rank="OP", title="片头曲", video_id="nm1", badge="OP"),
            ce.ChartRow(rank="1", title="榜单曲", video_id="sm1", score="100"),
            ce.ChartRow(rank="2", title="往期二", video_id="sm4", badge="HISTORY"),
            ce.ChartRow(rank="1", title="往期一", video_id="sm3", badge="HISTORY"),
            ce.ChartRow(rank="ED", title="片尾曲", video_id="nm2", badge="ED"),
            ce.ChartRow(rank="1", title="番外第一", video_id="sm2", score="90", section="番外")]))
        self.assertIn("|曲名 = 片头曲", text)
        self.assertIn("|本周 = OP", text)
        self.assertIn("|color = #AA0000", text)
        self.assertIn("|本周 = ED", text)
        self.assertIn("|color = #4FC1E9", text)
        self.assertIn("|bottom-column = {{color|#663300|H I S T O R Y}}", text)
        self.assertLess(text.index("片头曲"), text.index("榜单曲"))
        self.assertLess(text.index("榜单曲"), text.index("往期一"))
        self.assertLess(text.index("往期二"), text.index("片尾曲"))
        self.assertLess(text.index("片尾曲"), text.index("=== UTAU番外榜 ==="))

    def test_new_source_rows_are_skipped(self):
        """「新音源」原始残留行仍然不写（TOPIC 由带角标的行负责）。"""
        from utils import chart_editor as ce

        text = cw.render(self._work([
            ce.ChartRow(rank="1", title="榜单曲", video_id="sm1", score="100"),
            ce.ChartRow(rank="110", title="新音源曲", video_id="sm2", score="90",
                        section="新音源")]))
        self.assertIn("|曲名 = 榜单曲", text)
        self.assertNotIn("新音源曲", text)

    def test_extra_section_is_rendered_after_the_main_chart(self):
        from utils import chart_editor as ce

        text = cw.render(self._work([
            ce.ChartRow(rank="1", title="主榜第一", video_id="sm1", score="100"),
            ce.ChartRow(rank="1", title="番外第一", video_id="sm2", score="90", section="番外")]))
        self.assertIn("\n}}\n\n=== UTAU番外榜 ===\n{{VOCALOID_&_UTAU_Ranking/bricks", text)
        self.assertLess(text.index("主榜第一"), text.index("=== UTAU番外榜 ==="))
        self.assertLess(text.index("=== UTAU番外榜 ==="), text.index("番外第一"))


if __name__ == "__main__":
    unittest.main()
