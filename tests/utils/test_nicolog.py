"""测试 utils/nicolog.py：非公開 / 删稿视频的数据抓取（不联网）。"""
import json
import os
from datetime import datetime
from unittest import TestCase
from unittest import mock

from utils import nicolog

# 跑测时绝不真去开浏览器（同 tests/utils/test_vocadb_song.py；见 utils/browser_fetch.py）
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"

# 实测 https://www.nicolog.jp/watch/sm16693848（杰西卡，作者隐退后被设为非公開）的结构
PAGE = """<!DOCTYPE html><html><head>
<meta property="og:title" content="ニコログ｜【初音ミク】ジェシカ【オリジナル曲】" />
<meta property="og:image" content="https://tn.smilevideo.jp/smile?i=16693848" />
</head><body>
<div class="row"><div class="col-sm-10"><dl class="dl-horizontal">
<dt>動画ID</dt><dd><a href="https://www.nicovideo.jp/watch/sm16693848">sm16693848</a></dd>
<dt>動画タイトル</dt><dd>【初音ミク】ジェシカ【オリジナル曲】</dd>
<dt>投稿日時</dt><dd>2012年1月14日 20時55分27秒</dd>
<dt>長さ</dt><dd>0:03:48</dd>
<dt>投稿者</dt><dd><a href="/user/2377219">くるりんご (ID:2377219)</a></dd>
<dt>動画説明</dt><dd>おばんどす、くるりんごです！</dd>
</dl></div></div>
<script>
AmCharts.makeChart("chartdiv", {
  "graphs": [{"bullet":"round","id":"AmGraph-1","title":"再生数","valueField":"view"}],
  "dataProvider":[{"date":"2018-10-22 05:25","view":997335,"com":8610,"mylist":15388},
                  {"date":"2019-03-31 01:22","view":1081622,"com":8843,"mylist":15665}]});
</script>
<table class="table"><tr><th>取得日時</th><th>再生数</th></tr>
<tr><td class="date col-xs-2">2019年3月31日 1:22</td><td class="counter col-xs-1">1081622</td><td class="counter col-xs-1">8843</td><td class="counter col-xs-1">15665</td></tr>
<tr><td class="date col-xs-2">2018年10月22日 5:25</td><td class="counter col-xs-1">997335</td><td class="counter col-xs-1">8610</td><td class="counter col-xs-1">15388</td></tr>
</table></body></html>"""

# 表格顺序被打乱、且没有 dataProvider 时，要按日期取最新那一行
TABLE_ONLY = """<html><body><dl>
<dt>動画タイトル</dt><dd>测试曲</dd>
<dt>投稿日時</dt><dd>2020年9月4日 20時00分00秒</dd>
</dl>
<table><tr><td class="date">2020年9月10日 1:00</td><td class="counter">200</td><td class="counter">20</td><td class="counter">2</td></tr>
<tr><td class="date">2020年12月25日 23:00</td><td class="counter">500</td><td class="counter">50</td><td class="counter">5</td></tr>
<tr><td class="date">2020年10月1日 1:00</td><td class="counter">300</td><td class="counter">30</td><td class="counter">3</td></tr>
</table></body></html>"""

NOT_FOUND = "<html><head><title>ページが見つかりません</title></head><body></body></html>"

# og:image 还是活的域名（新视频）时要保留
PAGE_LIVE_THUMB = PAGE.replace("https://tn.smilevideo.jp/smile?i=16693848",
                              "https://nicovideo.cdn.nimg.jp/thumbnails/16693848/16693848.1")


class ParseDatetimeTest(TestCase):
    def test_japanese_datetime_is_converted_to_cn_date(self):
        self.assertEqual(datetime(2012, 1, 14), nicolog.parse_datetime("2012年1月14日 20時55分27秒"))

    def test_date_only(self):
        self.assertEqual(datetime(2020, 9, 4), nicolog.parse_datetime("2020年9月4日"))

    def test_timezone_is_applied_when_time_is_known(self):
        # 日本时间凌晨 → 东八区还是前一天
        self.assertEqual(datetime(2012, 1, 13), nicolog.parse_datetime("2012年1月14日 0時30分00秒"))
        self.assertEqual(datetime(2012, 1, 14), nicolog.parse_datetime("2012年1月14日 1時00分00秒"))

    def test_empty_or_invalid(self):
        self.assertIsNone(nicolog.parse_datetime(""))
        self.assertIsNone(nicolog.parse_datetime("unknown"))


class ParseTest(TestCase):
    def test_reads_video_fields(self):
        video = nicolog.parse(PAGE, "sm16693848")
        self.assertEqual("sm16693848", video.identifier)
        self.assertEqual("【初音ミク】ジェシカ【オリジナル曲】", video.title)
        self.assertEqual(datetime(2012, 1, 14), video.uploaded)
        self.assertEqual("くるりんご", video.uploader)
        self.assertEqual("0:03:48", video.duration)
        self.assertIn("おばんどす", video.description)
        # nicolog 给的是已停用的 <https://tn.smilevideo.jp/…>（下载必失败）→ 当作没有封面
        self.assertEqual("", video.thumbnail)

    def test_takes_latest_snapshot(self):
        video = nicolog.parse(PAGE, "sm16693848")
        self.assertEqual(1081622, video.views)
        self.assertEqual(8843, video.comments)
        self.assertEqual(15665, video.mylists)

    def test_table_is_used_without_snapshots(self):
        video = nicolog.parse(TABLE_ONLY, "sm37464090")
        self.assertEqual(500, video.views)          # 取日期最新的那一行
        self.assertEqual(50, video.comments)
        self.assertEqual(5, video.mylists)

    def test_missing_page_returns_none(self):
        self.assertIsNone(nicolog.parse(NOT_FOUND, "sm16693848"))
        self.assertIsNone(nicolog.parse("", "sm16693848"))

    def test_title_or_stats_alone_is_enough(self):
        self.assertTrue(nicolog.parse("<html><dl><dt>動画タイトル</dt><dd>曲</dd></dl></html>").title)
        self.assertTrue(nicolog.parse(TABLE_ONLY).available)

    def test_available_flag(self):
        video = nicolog.parse(PAGE, "sm16693848")
        self.assertTrue(video.available)
        self.assertFalse(nicolog.NicologVideo(identifier="sm1").available)


class UsableThumbnailTest(TestCase):
    """nicolog 的 og:image 多指向已停用的 tn.smilevideo.jp，不能拿来当封面下载地址。"""

    def test_dead_host_is_dropped(self):
        for url in ("https://tn.smilevideo.jp/smile?i=16693848",
                    "https://tn.smilevideo.jp/smile?i=16693848.1"):
            self.assertEqual("", nicolog.usable_thumbnail(url))

    def test_live_host_is_kept(self):
        url = "https://nicovideo.cdn.nimg.jp/thumbnails/37464090/37464090.9"
        self.assertEqual(url, nicolog.usable_thumbnail("  " + url + "  "))

    def test_empty(self):
        self.assertEqual("", nicolog.usable_thumbnail(""))
        self.assertEqual("", nicolog.usable_thumbnail(None))

    def test_parse_drops_dead_thumbnail(self):
        self.assertEqual("", nicolog.parse(PAGE, "sm16693848").thumbnail)
        self.assertEqual("https://nicovideo.cdn.nimg.jp/thumbnails/16693848/16693848.1",
                         nicolog.parse(PAGE_LIVE_THUMB, "sm16693848").thumbnail)


class FetchTest(TestCase):
    def test_fetch_uses_nicolog_url(self):
        response = mock.Mock(text=PAGE)
        with mock.patch.object(nicolog, "http_get", return_value=response) as http_get:
            video = nicolog.fetch("sm16693848")
        self.assertEqual(1081622, video.views)
        self.assertEqual("https://www.nicolog.jp/watch/sm16693848", http_get.call_args.args[0])
        self.assertTrue(http_get.call_args.kwargs["use_proxy"])

    def test_fetch_swallows_network_error(self):
        with mock.patch.object(nicolog, "http_get", side_effect=OSError("boom")), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=False), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html") as html:
            self.assertIsNone(nicolog.fetch("sm16693848"))
        html.assert_not_called()

    def test_fetch_without_identifier(self):
        with mock.patch.object(nicolog, "http_get") as http_get:
            self.assertIsNone(nicolog.fetch(""))
        http_get.assert_not_called()


class CloudflareFallbackTest(TestCase):
    """nicolog 被 Cloudflare 挡住（403「Just a moment...」）时借真浏览器取数。

    2026-10-06 用户报的「希望夏天能够延续」就是这个：直接请求拿不到 nicolog 的数据，
    非公開的两条稿件被当成正常投稿 —— 条目里没有 card 栏，投稿日退化成 VocaDB 的
    publishDate（8月5日），真实的 2023-08-04 / 10,174 再生全丢了。
    """

    CHALLENGE = "<html><head><title>Just a moment...</title></head><body>Just a moment...</body></html>"

    def test_challenge_falls_back_to_the_browser(self):
        blocked = mock.Mock(status_code=403, text=self.CHALLENGE)
        with mock.patch.object(nicolog, "http_get", return_value=blocked), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=True), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html", return_value=PAGE) as html:
            video = nicolog.fetch("sm16693848")
        self.assertEqual(1081622, video.views)
        self.assertEqual(datetime(2012, 1, 14), video.uploaded)
        html.assert_called_once_with("https://www.nicolog.jp/watch/sm16693848")

    def test_network_error_also_falls_back_to_the_browser(self):
        with mock.patch.object(nicolog, "http_get", side_effect=OSError("boom")), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=True), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html", return_value=PAGE):
            self.assertEqual(1081622, nicolog.fetch("sm16693848").views)

    def test_a_real_page_is_not_re_fetched_with_the_browser(self):
        """页面真的回来了（nicolog 上没有这条记录）→ 不必再折腾浏览器。"""
        missing = mock.Mock(status_code=200, text=NOT_FOUND)
        with mock.patch.object(nicolog, "http_get", return_value=missing), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=True), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html") as html:
            self.assertIsNone(nicolog.fetch("sm00000001"))
        html.assert_not_called()

    def test_without_a_browser_the_challenge_yields_nothing(self):
        blocked = mock.Mock(status_code=403, text=self.CHALLENGE)
        with mock.patch.object(nicolog, "http_get", return_value=blocked), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=False), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html") as html:
            self.assertIsNone(nicolog.fetch("sm16693848"))
        html.assert_not_called()

    def test_browser_page_without_the_record_yields_nothing(self):
        blocked = mock.Mock(status_code=503, text=self.CHALLENGE)
        with mock.patch.object(nicolog, "http_get", return_value=blocked), \
             mock.patch.object(nicolog.browser_fetch, "available", return_value=True), \
             mock.patch.object(nicolog.browser_fetch, "fetch_html", return_value=NOT_FOUND):
            self.assertIsNone(nicolog.fetch("sm00000001"))
