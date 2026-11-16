"""榜单条目的 nico 侧数据（见 utils/chart_nico.py）。"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from utils import chart_nico as cn

# 实测响应（sm8673630 = #52 第 1 名的 Lilium；老稿件没有 user_nickname）
THUMB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nicovideo_thumb_response status="ok"><thumb><video_id>sm8673630</video_id><title>【UTAU連続音】Lilium　【重音テト】</title><description>pixivですんげー美麗なテト絵</description><thumbnail_url>https://nicovideo.cdn.nimg.jp/thumbnails/8673630/8673630</thumbnail_url><first_retrieve>2009-11-01T02:39:15+09:00</first_retrieve><length>3:26</length><movie_type>mp4</movie_type><view_counter>151750</view_counter><comment_num>746</comment_num><mylist_counter>2066</mylist_counter><watch_url>https://www.nicovideo.jp/watch/sm8673630</watch_url><thumb_type>video</thumb_type><embeddable>1</embeddable><no_live_play>0</no_live_play><tags domain="jp"><tag lock="1">音楽</tag><tag lock="1">UTAU</tag><tag>重音テト</tag><tag>Lilium</tag></tags><genre>音楽・サウンド</genre></thumb></nicovideo_thumb_response>"""

THUMB_DELETED = '<nicovideo_thumb_response status="fail"><error><code>DELETED</code></error></nicovideo_thumb_response>'

# #52 榜单视频（sm8763104）的简介：老投稿是拍平的一整段
DESC_52 = ("○集計期間：2009年11月1日0時～2009年11月8日0時"
           "○集計対象動画期間：2009年10月24日18時～2009年11月7日18時"
           "○集計方法：再生数+コメント数×補正値+マイリスト登録数×20"
           "PL:終了 blogにて(http://utauhirohiro.blog56.fc2.com/blog-entry-447.html) "
           "週刊#51：sm8683932 週刊#53：sm8839634"
           "バックナンバー:mylist/10283783"
           "月刊UTAUランキング様のバックナンバー:mylist/9881007"
           "月刊UTAUオリジナルランキング様のバックナンバー:mylist/10402042"
           "UTAU情報交換所：http://asanutau.bbs.fc2.com/")
# 站上 #52 的「;视频简介」正文（逐字节对得上）
POEM_52 = ("○集計期間：2009年11月1日0時～2009年11月8日0時\n"
           "○集計対象動画期間：2009年10月24日18時～2009年11月7日18時\n"
           "○集計方法：再生数+コメント数×補正値+マイリスト登録数×20\n\n"
           "PL:終了 blogにて(http://utauhirohiro.blog56.fc2.com/blog-entry-447.html) "
           "週刊#51：sm8683932 週刊#53：sm8839634\n"
           "バックナンバー:mylist/10283783\n"
           "月刊UTAUランキング様のバックナンバー:mylist/9881007\n"
           "月刊UTAUオリジナルランキング様のバックナンバー:mylist/10402042\n\n"
           "UTAU情報交換所：http://asanutau.bbs.fc2.com/")


class NormalizeIdTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual("sm8673630", cn.normalize_id("sm8673630"))
        self.assertEqual("sm8673630", cn.normalize_id("8673630"))
        self.assertEqual("sm8673630", cn.normalize_id("https://www.nicovideo.jp/watch/sm8673630"))
        self.assertEqual("nm8676338", cn.normalize_id("nm8676338"))
        self.assertEqual("", cn.normalize_id(""))
        self.assertEqual("", cn.normalize_id("没有编号"))


class ThumbinfoTest(unittest.TestCase):
    def test_parses_the_real_response(self):
        info = cn.parse_thumbinfo(THUMB_XML)
        self.assertEqual("sm8673630", info["id"])
        self.assertEqual("【UTAU連続音】Lilium　【重音テト】", info["title"])
        self.assertEqual(151750, info["view"])
        self.assertEqual(746, info["comment"])
        self.assertEqual(2066, info["mylist"])
        self.assertEqual(206, info["length"])              # 3:26
        self.assertEqual("", info["uploader"])             # 老稿件没有 user_nickname

    def test_tags_drop_the_attributes(self):
        """`<tags domain="jp">` 不能被当成第一个 tag（曾经把 `<tag lock="1">音楽` 整段收进去）。"""
        self.assertEqual(["音楽", "UTAU", "重音テト", "Lilium"],
                         cn.parse_thumbinfo(THUMB_XML)["tags"])

    def test_time_conventions(self):
        info = cn.parse_thumbinfo(THUMB_XML)
        self.assertEqual("2009-11-01 02:39", cn.format_row_time(info["date_jst"]))
        self.assertEqual("2009年11月01日 01:39", cn.format_publish(info["date_jst"]))

    def test_deleted_video_returns_none(self):
        self.assertIsNone(cn.parse_thumbinfo(THUMB_DELETED))

    def test_network_failure_returns_none(self):
        with mock.patch.object(cn, "_get", side_effect=OSError("boom")):
            self.assertIsNone(cn.thumbinfo("sm1"))


class SearchTest(unittest.TestCase):
    def test_ids_come_out_in_order_and_unique(self):
        html = ('<a href="/watch/sm8673630">a</a><a href="/watch/sm8683940">b</a>'
                '<a href="/watch/sm8673630">dup</a><span>nm8676338</span>')
        self.assertEqual(["sm8673630", "sm8683940", "nm8676338"], cn.parse_search_ids(html))

    def test_limit_is_respected(self):
        html = "".join(f"<a>sm{9000000 + index}</a>" for index in range(30))
        self.assertEqual(5, len(cn.parse_search_ids(html, limit=5)))

    def test_search_failure_is_empty(self):
        with mock.patch.object(cn, "search_videos", return_value=[]), \
             mock.patch.object(cn, "_get", side_effect=OSError("boom")):
            self.assertEqual([], cn.search_ids("Lilium"))

    def test_snapshot_hits_are_used_first(self):
        """快照接口有结果就不再打 HTML 搜索页（她那条接口查无结果是 404，会刷屏）。"""
        with mock.patch.object(cn, "search_videos",
                               return_value=[{"id": "sm8673630"}]), \
             mock.patch.object(cn, "_get", side_effect=AssertionError("不该走HTML搜索")):
            self.assertEqual(["sm8673630"], cn.search_ids("Lilium"))

    def test_zero_result_404_is_quiet(self):
        """查无结果时 HTML 搜索页回 404 —— 那是「没有结果」，不能写 warning。"""
        error = OSError("404 Client Error")
        error.response = mock.Mock(status_code=404)
        with mock.patch.object(cn, "search_videos", return_value=[]), \
             mock.patch.object(cn, "_get", side_effect=error), \
             self.assertLogs(level="DEBUG") as captured:
            self.assertEqual([], cn.search_ids("zzzzqqqqxxx"))
        self.assertFalse([line for line in captured.output if "WARNING" in line])


class BestMatchTest(unittest.TestCase):
    def test_picks_the_closest_title(self):
        infos = {"sm1": {"id": "sm1", "title": "【UTAU】Lilium【重音テト】"},
                 "sm2": {"id": "sm2", "title": "ぜんぜん違う曲"}}
        with mock.patch.object(cn, "thumbinfo", side_effect=lambda vid: infos.get(vid)):
            self.assertEqual("sm1", cn.best_match("Lilium", ["sm1", "sm2"])["id"])

    def test_unrelated_titles_are_rejected(self):
        infos = {"sm1": {"id": "sm1", "title": "ぜんぜん違う曲"}}
        with mock.patch.object(cn, "thumbinfo", side_effect=lambda vid: infos.get(vid)):
            self.assertIsNone(cn.best_match("Lilium", ["sm1"], threshold=0.8))


class RankingVideoTest(unittest.TestCase):
    def test_index_from_title(self):
        self.assertEqual(52, cn.parse_index("週刊UTAUランキング　#52"))
        self.assertEqual(813, cn.parse_index("週刊UTAUランキング#813"))
        self.assertEqual(0, cn.parse_index("週刊UTAUランキング"))

    def test_existing_page_wins(self):
        with mock.patch.object(cn, "page_video_id", return_value="sm8763104"):
            self.assertEqual(("sm8763104", "站上已有页面"), cn.find_ranking_video(self.spec(), 52))

    def test_falls_back_to_search(self):
        with mock.patch.object(cn, "page_video_id", return_value=""), \
             mock.patch.object(cn, "search_videos",
                               return_value=[{"id": "sm1", "title": "週刊UTAUランキング #53"},
                                             {"id": "sm2", "title": "週刊UTAUランキング #52"}]):
            self.assertEqual(("sm2", "niconico搜索"), cn.find_ranking_video(self.spec(), 52))

    def test_gives_up_when_nothing_matches(self):
        with mock.patch.object(cn, "page_video_id", return_value=""), \
             mock.patch.object(cn, "search_videos", return_value=[]), \
             mock.patch.object(cn, "search_ids", return_value=[]):
            self.assertEqual(("", ""), cn.find_ranking_video(self.spec(), 52))

    def test_other_issue_is_not_accepted(self):
        """搜出来的都是别的期（#53 的搬运也没有），不能拿来当第 52 期。"""
        with mock.patch.object(cn, "page_video_id", return_value=""), \
             mock.patch.object(cn, "search_videos",
                               return_value=[{"id": "sm3", "title": "週刊UTAUランキング #53"}]), \
             mock.patch.object(cn, "search_ids", return_value=[]):
            self.assertEqual(("", ""), cn.find_ranking_video(self.spec(), 52))

    @staticmethod
    def spec():
        return cn.CHARTS["utau"]


class PeriodTest(unittest.TestCase):
    def test_flat_old_description(self):
        self.assertEqual(("2009年11月01日 00:00", "2009年11月08日 00:00"),
                         cn.parse_period(DESC_52))

    def test_modern_description_with_a_different_layout(self):
        text = "○ 集計期間\n　・UTAU ：2024年6月1日0時～2024年6月8日0時（投稿2週間以内）"
        self.assertEqual(("2024年06月01日 00:00", "2024年06月08日 00:00"), cn.parse_period(text))

    def test_missing_period(self):
        self.assertEqual(("", ""), cn.parse_period("集計期間なし"))


class SplitDescriptionTest(unittest.TestCase):
    def test_old_flat_description_matches_the_site(self):
        self.assertEqual(POEM_52, cn.split_description(DESC_52))

    def test_blank_line_before_the_links(self):
        text = "前置きK!!te Playlist LINK！PL：mylist/1週刊UTAUランキングまとめ　http://x/"
        self.assertEqual("前置き\n\nK!!te Playlist LINK！\n\nPL：mylist/1\n\n"
                         "週刊UTAUランキングまとめ　http://x/", cn.split_description(text))

    def test_existing_newlines_are_kept(self):
        self.assertEqual("一行目\n二行目", cn.split_description("一行目\n二行目"))

    def test_empty(self):
        self.assertEqual("", cn.split_description(""))


class VocalistTest(unittest.TestCase):
    def test_known_names_are_translated(self):
        self.assertEqual("重音Teto", cn.vocalists_from_tags(["UTAU", "重音テト"]))
        self.assertEqual("初音未来", cn.vocalists_from_tags(["初音ミク"]))
        self.assertEqual("重音Teto、初音未来",
                         cn.vocalists_from_tags(["UTAU", "重音テト", "初音ミク"]))

    def test_unknown_tags_are_ignored(self):
        self.assertEqual("", cn.vocalists_from_tags(["UTAU", "オリジナル曲"]))


class FillRowTest(unittest.TestCase):
    INFO = {"id": "sm8673630", "title": "【UTAU連続音】Lilium　【重音テト】",
            "date_jst": datetime(2009, 11, 1, 2, 39, tzinfo=timezone(timedelta(hours=9))),
            "uploader": "むのーP", "tags": ["UTAU", "重音テト"]}

    def test_known_id_fills_time_producer_and_vocalist(self):
        with mock.patch.object(cn, "thumbinfo", return_value=self.INFO):
            row = cn.fill_row({"id": "sm8673630", "曲名": "Lilium", "本周": "1"})
        self.assertEqual("2009-11-01 02:39", row["时间"])
        self.assertEqual("むのーP", row["P主"])
        self.assertEqual("重音Teto", row["歌姬"])

    def test_existing_values_are_kept(self):
        with mock.patch.object(cn, "thumbinfo", return_value=self.INFO):
            row = cn.fill_row({"id": "sm8673630", "曲名": "Lilium", "P主": "别人", "时间": "x"})
        self.assertEqual("别人", row["P主"])
        self.assertEqual("x", row["时间"])

    def test_search_by_title(self):
        with mock.patch.object(cn, "thumbinfo", return_value=self.INFO), \
             mock.patch.object(cn, "search_ids", return_value=["sm8673630"]):
            row = cn.fill_row({"曲名": "Lilium"})
        self.assertEqual("sm8673630", row["id"])

    def test_nothing_found_leaves_it_empty(self):
        with mock.patch.object(cn, "thumbinfo", return_value=None), \
             mock.patch.object(cn, "search_ids", return_value=[]):
            row = cn.fill_row({"曲名": "无此曲"})
        self.assertNotIn("id", [key for key, value in row.items() if value])


class BilibiliTest(unittest.TestCase):
    def test_uses_a_candidate_whose_title_has_this_issue(self):
        items = [{"bvid": "BV1wrong", "title": "週刊<em>UTAU</em>ランキング #152"},
                 {"bvid": "BV1right", "title": "週刊<em>UTAU</em>ランキング #52"}]
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch("utils.producer_template.bilibili_search_items", return_value=items):
            self.assertEqual("BV1right", cn.find_bilibili(cn.CHARTS["utau"], 52))

    def test_ignores_unrelated_and_case_mismatched_results(self):
        items = [{"bvid": "BV1x", "title": "周刊虚拟歌手中文曲排行榜♪744"},
                 {"bvid": "BV1y", "title": "週刊VOCALOID #52"}]
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch("utils.producer_template.bilibili_search_items", return_value=items):
            self.assertEqual("", cn.find_bilibili(cn.CHARTS["utau"], 52))

    def test_search_failure_is_empty(self):
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch("utils.producer_template.bilibili_search_items",
                        side_effect=OSError("boom")):
            self.assertEqual("", cn.find_bilibili(cn.CHARTS["utau"], 52))

    def test_existing_page_wins_over_search(self):
        """站上这一期的页面里已经写了 `{{BilibiliVideo|id=…}}` 就直接抄（最准）。"""
        with mock.patch.object(cn, "page_bilibili", return_value="BV1GZ4y1x7Zo"), \
             mock.patch("utils.producer_template.bilibili_search_items",
                        side_effect=AssertionError("页面已有就不该再搜")):
            self.assertEqual("BV1GZ4y1x7Zo", cn.find_bilibili(cn.CHARTS["utau"], 52))

    def test_asks_once_when_search_fails(self):
        """搜不到时问用户一次，填的 BV 号就用上（#53 的 `BV11e411x7qq` 就这么来的）。"""
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch("utils.producer_template.bilibili_search_items", return_value=[]):
            found = cn.find_bilibili(cn.CHARTS["utau"], 53,
                                     ask=lambda: "https://www.bilibili.com/video/BV11e411x7qq")
        self.assertEqual("BV11e411x7qq", found)

    def test_no_index_means_no_search(self):
        with mock.patch.object(cn, "page_bilibili",
                               side_effect=AssertionError("没期数不该查站上页面")):
            self.assertEqual("", cn.find_bilibili(cn.CHARTS["utau"], 0))


if __name__ == "__main__":
    unittest.main()
