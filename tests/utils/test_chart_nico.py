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


class DeletedRowTest(unittest.TestCase):
    """削除 / 非公開 的稿件走 nicolog（用户 2026-11-16：「尝试通过 nicolog 获取」）。"""

    class Record:
        title = "【Voc@loidM@ster祭り3】アイマスの3人で驫麤～とりぷるばか～【トレスMAD】"
        uploaded = datetime(2009, 11, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        uploaded_cn = datetime(2009, 11, 9, 2, 19)
        views = 0
        comments = 0
        mylists = 0
        uploader = "かまやP"
        description = "元ネタは動画sm3787346です"
        available = True

    def test_time_and_cover_come_from_nicolog(self):
        with mock.patch.object(cn, "thumbinfo", return_value=None), \
             mock.patch.object(cn, "nicolog") as fake:
            fake.fetch.return_value = self.Record()
            row = cn.fill_row({"id": "sm8752783", "曲名": "アイマス"})
        self.assertEqual("sm8752783", row["id"])
        self.assertEqual("2009-11-09 03:19", row["时间"], "nicolog 给的是东八区，要换回 JST")
        self.assertEqual("1", row["翻唱"])
        self.assertIn("已削除", row.get("备注", ""))
        self.assertTrue(row.get(cn.NO_INFO_KEY), "删稿的元数据不可用，要标记给上游换线索")

    def test_cover_falls_back_to_the_playlist_title(self):
        """nicolog 只剩「已削除」时，用播放列表抄下来的标题判（用户 2026-11-17 的几首）。"""
        record = type("Rec", (), dict(self.Record.__dict__))()
        record.description = "この動画は削除されました"
        record.title = ""
        record.uploaded_cn = None                  # 连投稿时间都没有 → 整条当作取不到
        with mock.patch.object(cn, "thumbinfo", return_value=None), \
             mock.patch.object(cn, "nicolog") as fake:
            fake.fetch.return_value = record
            row = cn.fill_row({"id": "sm8785048", "曲名": "You",
                               "raw_title": "【嘘音ズニ】　You　【UTAU】"}, search=False)
        self.assertNotIn("翻唱", [key for key, value in row.items() if value],
                         "标题里没有任何翻唱字样时不要瞎标")
        self.assertTrue(row.get(cn.NO_INFO_KEY), "取不到元数据要有标记，好让上游换线索")
        with mock.patch.object(cn, "thumbinfo", return_value=None), \
             mock.patch.object(cn, "nicolog") as fake:
            fake.fetch.return_value = None
            fake.fetch_nvapi.return_value = None
            covered = cn.fill_row({"id": "nm8753301", "曲名": "自作音源で『サイハテ』カバー"})
        self.assertEqual("1", covered["翻唱"], "标题里写着「カバー」就该标翻唱")

    def test_unknown_rows_do_not_get_a_note(self):
        with mock.patch.object(cn, "thumbinfo", return_value=None), \
             mock.patch.object(cn, "nicolog") as fake, \
             mock.patch.object(cn, "search_ids", return_value=[]):
            fake.fetch.return_value = None
            fake.fetch_nvapi.return_value = None
            row = cn.fill_row({"曲名": "无此曲"})
        self.assertNotIn("备注", row)


class BilibiliTest(unittest.TestCase):
    def test_uses_a_candidate_whose_title_has_this_issue(self):
        items = [{"bvid": "BV1wrong", "title": "週刊<em>UTAU</em>ランキング #152"},
                 {"bvid": "BV1right", "title": "週刊<em>UTAU</em>ランキング #52"}]
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch("utils.producer_template.bilibili_search_items", return_value=items):
            self.assertEqual("BV1right", cn.find_bilibili(cn.CHARTS["utau"], 52))

    def test_target_beyond_the_first_ten_is_found(self):
        """实测：`週刊UTAU Ranking #53` 的搜索结果里 `BV11e411x7qq` 排在第 11 条。

        旧的 `limit=10` 正好把它切掉 —— 现在取整页 20 条自己筛。
        """
        items = [{"bvid": f"BV1noise{index:02d}", "title": f"无关视频{index}"}
                 for index in range(10)]
        items.append({"bvid": "BV11e411x7qq",
                      "title": "週刊<em>UTAU</em> <em>Ranking</em> #<em>53</em>(和谐版)"})
        items.extend({"bvid": f"BV1more{index:02d}", "title": f"别的{index}"}
                     for index in range(9))
        calls = []

        def fake_search(keyword, limit=0, purify=None, page=1):
            calls.append((keyword, limit, purify, page))
            return items

        with mock.patch("utils.producer_template.bilibili_search_items",
                        side_effect=fake_search):
            found = cn._search_bilibili("週刊UTAU Ranking #53", 53)
        self.assertEqual("BV11e411x7qq", found)
        keyword, limit, purify, page = calls[0]
        self.assertEqual("週刊UTAU Ranking #53", keyword)
        self.assertEqual(cn.BILIBILI_PAGE_SIZE, limit)
        self.assertFalse(purify)                 # 净化会按相关度重排，这里要整页原样
        self.assertEqual(1, page)

    def test_english_keyword_comes_first_and_pages_are_walked(self):
        tried = []

        def fake_search(keyword, index, page=1):
            tried.append((keyword, page))
            return "BV11e411x7qq" if (keyword, page) == ("週刊UTAU Ranking #53", 2) else ""

        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch.object(cn, "_search_bilibili", side_effect=fake_search):
            found = cn.find_bilibili(cn.CHARTS["utau"], 53)
        self.assertEqual("BV11e411x7qq", found)
        self.assertEqual(("週刊UTAU Ranking #53", 1), tried[0])
        self.assertEqual(("週刊UTAU Ranking #53", 2), tried[1])

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
        """搜不到时才问用户一次，填的 BV 号就用上（#53 的 `BV11e411x7qq` 就这么来的）。"""
        with mock.patch.object(cn, "page_bilibili", return_value=""), \
             mock.patch.object(cn, "_search_bilibili", return_value=""):
            found = cn.find_bilibili(cn.CHARTS["utau"], 53,
                                     ask=lambda: "https://www.bilibili.com/video/BV11e411x7qq")
        self.assertEqual("BV11e411x7qq", found)

    def test_no_index_means_no_search(self):
        with mock.patch.object(cn, "page_bilibili",
                               side_effect=AssertionError("没期数不该查站上页面")):
            self.assertEqual("", cn.find_bilibili(cn.CHARTS["utau"], 0))


class CoverTest(unittest.TestCase):
    """「翻唱」判断：只看 n 站的标题 / tag / 简介（用户 2026-11-16 的要求）。"""

    def test_cover_in_title_or_tags(self):
        self.assertTrue(cn.looks_like_cover(
            {"title": "【UTAUカバー】La voix【重音テト】", "tags": ["UTAU"]}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "La voix", "tags": ["UTAU", "歌ってみた"]}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "Lilium", "tags": ["UTAUカバー曲"], "description": ""}))

    def test_cover_only_in_the_description(self):
        self.assertTrue(cn.looks_like_cover(
            {"title": "けんか別れ", "tags": ["UTAU"],
             "description": "本家様よりお借りしました"}))

    def test_description_saying_someone_sang_it(self):
        """用户 2026-11-19：第 61 期第 11 名只有简介里写着「…をドナルドに歌ってもらいました」。"""
        self.assertTrue(cn.looks_like_cover(
            {"title": "【UTAU】 初音ミクの消失 -DEAD END- 【ドナルド】",
             "tags": ["音楽", "UTAU", "ごぼうP", "ドナルド", "初音ミクの消失"],
             "description": "「ドナルド手紙」から抽出した音源を使い、初音ミクの消失を"
                            "ドナルドに歌ってもらいました。早口部分はUTAUだとやっぱりキツイかなぁ"}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "ある曲", "tags": ["UTAU"],
             "description": "この曲をテトに歌わせてみました"}))

    def test_original_is_not_a_cover(self):
        self.assertFalse(cn.looks_like_cover(
            {"title": "【重音テト】世界の終わり【オリジナル曲】", "tags": ["UTAU"],
             "description": "オリジナル曲です"}))
        self.assertFalse(cn.looks_like_cover({"title": "白花", "tags": ["UTAU"]}))
        self.assertFalse(cn.looks_like_cover(None))

    def test_description_that_points_at_a_source_video(self):
        # 实测两首：『曲はsm4414892をショートバージョンに編集』/ nicolog 里的『元ネタは…sm3787346』
        self.assertTrue(cn.looks_like_cover(
            {"title": "【MMD】和音マコで『ワールドイズマイン』",
             "tags": ["MikuMikuDance", "ワールドイズマイン"],
             "description": "曲はsm4414892をショートバージョンに編集。"}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "アイマスの3人で驫麤～とりぷるばか～", "tags": [],
             "description": "元ネタは動画sm3787346です"}))

    def test_title_markers_reported_by_the_user(self):
        """用户 2026-11-17 点名的几首：`トレスMAD`・`歌ってもら`・`UTAUカバー`。"""
        self.assertTrue(cn.looks_like_cover(
            {"title": "【Voc@loidM@ster祭り3】アイマスの3人で驫麤～とりぷるばか～【トレスMAD】"}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "【UTAU】SHOICHIROに 「炉心融解」 を歌ってもらいました"}))
        self.assertTrue(cn.looks_like_cover(
            {"title": "【新音源】 綿歌チトセ ／ Fire◎Flower 【UTAUカバー】"}))
        self.assertTrue(cn.looks_like_cover({"title": "【UTAU】右肩の蝶 歌わせてみた【井上 サユリ】"}))
        self.assertFalse(cn.looks_like_cover({"title": "【嘘音ズニ】　You　【UTAU】"}))

    def test_fill_row_marks_cover_but_keeps_a_manual_value(self):
        info = {"id": "sm8673630", "title": "【UTAUカバー】La voix", "tags": ["UTAUカバー曲"],
                "description": "", "uploader": "P", "date_jst": None}
        with mock.patch.object(cn, "thumbinfo", return_value=info):
            row = cn.fill_row({"id": "sm8673630", "曲名": "La voix"})
        self.assertEqual("1", row["翻唱"])
        with mock.patch.object(cn, "thumbinfo", return_value=info):
            typed = cn.fill_row({"id": "sm8673630", "曲名": "La voix", "翻唱": "0"})
        self.assertEqual("0", typed["翻唱"])      # 用户填过的不动


class SearchWindowTest(unittest.TestCase):
    """投稿时间窗过滤 + 分级关键词（2026-11-16 用真机数据实测定下来的）。"""

    def test_window_is_sent_as_start_time_filters(self):
        sent = {}

        def fake_get(url, **kwargs):
            sent.update(kwargs.get("params") or {})
            return mock.Mock(status_code=200, json=lambda: {"data": []})

        start = datetime(2009, 10, 31, 18, tzinfo=cn.JST)
        end = datetime(2009, 11, 14, 18, tzinfo=cn.JST)
        with mock.patch.object(cn, "http_get", side_effect=fake_get):
            cn.search_videos("UTAU三人娘", window=(start, end))
        self.assertIn("filters[startTime][gte]", sent)
        self.assertIn("filters[startTime][lte]", sent)
        self.assertTrue(sent["filters[startTime][gte]"].startswith("2009-10-24"))

    def test_queries_shorten_long_titles_and_keep_the_window(self):
        title = "UTAU三人娘でGOMYWAY!!"
        queries = cn._queries({"曲名": title}, title)
        keywords = [keyword for keyword, _targets, _window in queries]
        self.assertIn("UTAU三人娘で", keywords)      # 实测这条 + 时间窗只剩 1 条、正是要的稿件
        self.assertTrue(any(window for _keyword, _targets, window in queries))
        self.assertLessEqual(len(queries), cn.MAX_QUERIES)

    def test_tail_noise_is_stripped(self):
        self.assertEqual("オービタル手描きP V", cn._strip_tail_noise("オービタル手描きP V…"))

    def test_vocalist_keys_map_back_to_japanese(self):
        self.assertEqual(["重音Teto", "重音テト"], cn._vocalist_keys({"歌姬": "重音Teto"}))

    def test_exact_title_outside_the_window_is_not_trusted(self):
        """`Lilium` 有一条 2008 年的同名稿件 —— 不能因为「标题一样」就直接采用。"""
        start = datetime(2009, 10, 31, 18, tzinfo=cn.JST)
        end = datetime(2009, 11, 14, 18, tzinfo=cn.JST)
        window = (start, end)
        early = {"id": "sm2153645", "title": "Lilium", "tags": [],
                 "date_jst": datetime(2008, 1, 27, tzinfo=cn.JST)}
        right = {"id": "sm8673630", "title": "【UTAU連続音】Lilium　【重音テト】",
                 "tags": ["重音テト"], "date_jst": datetime(2009, 11, 1, 2, 39, tzinfo=cn.JST)}

        def fake_search(keyword, targets="title", limit=10, window=(None, None)):
            return [early, right] if window[0] else [early, right]

        infos = {"sm2153645": dict(early), "sm8673630": dict(right)}
        with mock.patch.object(cn, "search_videos", side_effect=fake_search), \
             mock.patch.object(cn, "_verify", side_effect=lambda vid: infos.get(vid)):
            found = cn._pick_candidate({"曲名": "Lilium", "歌姬": "重音Teto"}, "Lilium", window)
        self.assertEqual("sm8673630", found["id"])

    def test_loose_match_is_flagged_for_review(self):
        """「短词 + 时间窗」只到 0.55 的匹配会被标成待核对，而不是当没找到。"""
        start = datetime(2009, 10, 31, 18, tzinfo=cn.JST)
        end = datetime(2009, 11, 14, 18, tzinfo=cn.JST)
        item = {"id": "sm1", "title": "【重音テト（とか）】オービタル　手描きＰＶ",
                "tags": [], "date_jst": datetime(2009, 11, 7, tzinfo=cn.JST)}
        with mock.patch.object(cn, "search_videos", return_value=[item]), \
             mock.patch.object(cn, "_similar", return_value=0.6), \
             mock.patch.object(cn, "_verify", return_value=dict(item)):
            found = cn._pick_candidate({"曲名": "オービタル手描きP V"}, "オービタル手描きP V",
                                      (start, end))
        self.assertEqual("sm1", found["id"])
        self.assertTrue(found.get("loose"))


class SnapshotTest(unittest.TestCase):
    """快照搜索接口（HTML 搜索页查无结果会 404，这条不会）。"""

    PAYLOAD = {"meta": {"totalCount": 1, "id": "x"}, "data": [
        {"contentId": "sm8673630", "title": "【UTAU連続音】Lilium　【重音テト】",
         "startTime": "2009-11-01T02:39:15+09:00", "viewCounter": 151750,
         "commentCounter": 746, "mylistCounter": 2066, "tags": "UTAU 重音テト Lilium"},
        {"title": "没有编号的那条"},
        "不是字典",
    ]}

    def test_parses_the_fields_we_use(self):
        found = cn.parse_snapshot(self.PAYLOAD)
        self.assertEqual(1, len(found))
        item = found[0]
        self.assertEqual("sm8673630", item["id"])
        self.assertEqual(["UTAU", "重音テト", "Lilium"], item["tags"])
        self.assertEqual(151750, item["view"])
        self.assertEqual("2009-11-01 02:39", cn.format_row_time(item["date_jst"]))

    def test_garbage_is_empty(self):
        self.assertEqual([], cn.parse_snapshot({}))
        self.assertEqual([], cn.parse_snapshot(None))
        self.assertEqual([], cn.parse_snapshot({"data": []}))

    def test_search_always_sends_a_sort_and_an_empty_keyword_is_free(self):
        """`_sort` 是必填参数（不传接口 400）；空关键词直接返回，不发请求。"""
        sent = {}

        def fake_get(url, **kwargs):
            sent.update(kwargs.get("params") or {})
            return mock.Mock(status_code=200, json=lambda: {"data": []})

        with mock.patch.object(cn, "http_get", side_effect=fake_get):
            cn.search_videos("Lilium")
        self.assertEqual("-viewCounter", sent["_sort"])
        self.assertIn("contentId", sent["fields"])
        with mock.patch.object(cn, "http_get", side_effect=AssertionError("不该发请求")):
            self.assertEqual([], cn.search_videos("   "))

    def test_search_failure_is_empty(self):
        with mock.patch.object(cn, "http_get", side_effect=OSError("boom")):
            self.assertEqual([], cn.search_videos("Lilium"))
        with mock.patch.object(cn, "http_get", return_value=mock.Mock(status_code=503)):
            self.assertEqual([], cn.search_videos("Lilium"))


class CleanTitleTest(unittest.TestCase):
    def test_brackets_are_dropped(self):
        self.assertEqual("世界の終わり",
                         cn.clean_song_title("【重音テト】世界の終わり【オリジナル】"))

    def test_other_brackets_are_kept(self):
        self.assertEqual("少年期（ドラえもんのやつ）", cn.clean_song_title("少年期（ドラえもんのやつ）"))
        # 句内的 `『』` 是标题的一部分（用户 2026-11-16 的批注）
        self.assertEqual("古川Pの『Alice』をカバーしたんだぜ",
                         cn.clean_song_title("古川Pの『Alice』をカバーしたんだぜ"))

    def test_a_title_wrapped_in_quotes_loses_the_pair(self):
        # `『炉心融解(Hard-R.K.mix)』` 整句都被 `『』` 包住 → 去掉那对引号
        self.assertEqual("炉心融解(Hard-R.K.mix)", cn.clean_song_title("『炉心融解(Hard-R.K.mix)』"))
        self.assertEqual("Alice", cn.clean_song_title("『Alice』"))

    def test_cover_tokens_are_stripped(self):
        # 用户 2026-11-16：「我要剥的是 ～カバー～」
        self.assertEqual("Packaged", cn.clean_song_title("Packaged ～カバー～"))
        self.assertEqual("Packaged 再エンコ版",
                         cn.clean_song_title("【重音テト】Packaged  ～カバー～【UTAU】再エンコ版"))
        self.assertEqual("La voix", cn.clean_song_title("【重音テト】La voix（カバー）"))

    def test_a_title_that_is_only_brackets_is_kept(self):
        self.assertEqual("【歌ってみた】", cn.clean_song_title("【歌ってみた】"))


class VocalistNameTest(unittest.TestCase):
    def test_chart_names_use_the_site_spelling(self):
        self.assertEqual("重音Teto、初音未来", cn.normalize_vocalists("重音テト、初音ミク"))
        self.assertEqual("和音Mako", cn.normalize_vocalists("和音マコ"))

    def test_names_come_out_of_the_title_brackets(self):
        self.assertEqual("重音Teto", cn.vocalists_from_title("【重音テト】La voix【UTAUカバー】"))
        self.assertEqual("", cn.vocalists_from_title("【オリジナル】ぜんぜん違う曲"))


class AmbiguousCandidateTest(unittest.TestCase):
    """同名曲有好几个稿件时**不猜**：返回 `ambiguous`，让用户在表格里挑。"""

    def test_two_equally_good_candidates_stay_empty(self):
        # 两条都不是「完全同名」（相似度 0.9 < TITLE_CLEAR），分数又只差 0 —— 于是不猜
        items = [{"id": "sm1", "title": "Lilium (cover)", "tags": [], "date_jst": None},
                 {"id": "sm2", "title": "Lilium 歌ってみた", "tags": [], "date_jst": None}]
        with mock.patch.object(cn, "search_videos", return_value=items), \
             mock.patch.object(cn, "thumbinfo", side_effect=AssertionError("不该去复核")):
            found = cn._pick_candidate({"曲名": "Lilium"}, "Lilium", (None, None))
        self.assertEqual({"ambiguous": True}, found)

    def test_fill_row_flags_it(self):
        with mock.patch.object(cn, "_pick_candidate", return_value={"ambiguous": True}):
            row = cn.fill_row({"曲名": "Lilium"})
        self.assertTrue(row.get("ambiguous"))
        self.assertFalse(row.get("id"))

    def test_a_clear_winner_is_verified(self):
        items = [{"id": "sm1", "title": "Lilium", "tags": [], "date_jst": None},
                 {"id": "sm2", "title": "ぜんぜん違う曲", "tags": [], "date_jst": None}]
        with mock.patch.object(cn, "search_videos", return_value=items), \
             mock.patch.object(cn, "thumbinfo", return_value={"id": "sm1", "tags": []}):
            self.assertEqual("sm1", cn._pick_candidate({"曲名": "Lilium"}, "Lilium",
                                                       (None, None))["id"])


class OwnerTest(unittest.TestCase):
    def test_owner_name_is_read_from_nvapi(self):
        payload = {"data": {"items": [{"video": {"owner": {"name": "DJ横山"}}}]}}
        with mock.patch.object(cn, "http_get", return_value=mock.Mock(
                status_code=200, json=lambda: payload)):
            self.assertEqual("DJ横山", cn.nvapi_owner("sm8780736"))

    def test_failure_is_empty(self):
        self.assertEqual("", cn.nvapi_owner(""))
        with mock.patch.object(cn, "http_get", side_effect=OSError("boom")):
            self.assertEqual("", cn.nvapi_owner("sm8780736"))


if __name__ == "__main__":
    unittest.main()
