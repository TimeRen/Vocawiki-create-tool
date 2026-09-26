"""models/video.py 的解析测试（不联网）。

背景：YouTube 页面上有多个 interactionStatistic 块，**第一条是点赞数**、第二条才是播放量。
旧实现取第一个 `meta[itemprop="userInteractionCount"]` 当播放量，把点赞当播放，
导致「明明是殿堂曲却没有 {{虚拟歌手歌曲荣誉题头}}」（实测 287245 播放 / 6768 点赞）。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from bs4 import BeautifulSoup

from models import video
from models.video import get_bv


class TestVide(TestCase):
    def test_get_bv(self):
        self.assertEqual("BV1Ex411w7d2",
                         get_bv("https://bilibili.com/video/BV1Ex411w7d2/?spm_id_from"))

        self.assertEqual("BV1Ex411w7d2",
                         get_bv("https://bilibili.com/video/BV1Ex411w7d2"))

        self.assertEqual("BV1Ex411w7d2",
                         get_bv("BV1Ex411w7d2"))


def _counter(kind: str, value) -> str:
    """照抄 YouTube 页面的写法：一个 interactionStatistic 块 + 两个 meta。"""
    return (f'<div itemprop="interactionStatistic" itemscope '
            f'itemtype="https://schema.org/InteractionCounter">'
            f'<meta itemprop="interactionType" content="https://schema.org/{kind}">'
            f'<meta itemprop="userInteractionCount" content="{value}"></div>')


# 真实的 YouTube 页面：第一个计数是点赞、第二个才是播放量
PAGE = ('<html><head><meta itemprop="datePublished" content="2024-02-23">'
        '</head><body>' + _counter("LikeAction", 6768) + _counter("WatchAction", 287245) +
        '</body></html>')


class YouTubeViewCountTest(TestCase):
    def test_meta_picks_watch_action_not_likes(self):
        soup = BeautifulSoup(PAGE, "html.parser")
        self.assertEqual(287245, video.parse_yt_view_count(soup))

    def test_meta_ignores_order_of_counters(self):
        # 播放量排在前面时也要拿播放量
        html = ("<html><body>" + _counter("WatchAction", 287245) + _counter("LikeAction", 6768) +
                "</body></html>")
        self.assertEqual(287245, video.parse_yt_view_count(BeautifulSoup(html, "html.parser")))

    def test_legacy_interaction_count_meta(self):
        html = '<meta itemprop="interactionCount" content="1,234">'
        self.assertEqual(1234, video.parse_yt_view_count(BeautifulSoup(html, "html.parser")))

    def test_no_counter_returns_none(self):
        soup = BeautifulSoup("<html><body><p>nothing here</p></body></html>", "html.parser")
        self.assertIsNone(video.parse_yt_view_count(soup))

    def test_get_yt_info_reports_views_not_likes(self):
        response = SimpleNamespace(text=PAGE, raise_for_status=lambda: None)
        with mock.patch.object(video, "http_get", return_value=response):
            result = video.get_yt_info("yqw4Qcv29FY")
        self.assertEqual(287245, result.views)
        self.assertEqual(datetime(2024, 2, 23), result.uploaded)

    def test_get_yt_info_falls_back_to_ld_json(self):
        metadata = ('{"@type":"VideoObject","uploadDate":"2024-02-23",'
                    '"interactionStatistic":['
                    '{"interactionType":"https://schema.org/LikeAction",'
                    '"userInteractionCount":6768},'
                    '{"interactionType":"https://schema.org/WatchAction",'
                    '"userInteractionCount":287245}]}')
        page = ('<html><head><script type="application/ld+json">' + metadata +
                '</script></head><body>no meta counter</body></html>')
        response = SimpleNamespace(text=page, raise_for_status=lambda: None)
        with mock.patch.object(video, "http_get", return_value=response):
            result = video.get_yt_info("yqw4Qcv29FY")
        self.assertEqual(287245, result.views)
        self.assertEqual(datetime(2024, 2, 23), result.uploaded)

    def test_get_yt_info_without_any_count_raises(self):
        page = ('<html><head><meta itemprop="datePublished" content="2024-02-23">'
                '</head><body></body></html>')
        response = SimpleNamespace(text=page, raise_for_status=lambda: None)
        with mock.patch.object(video, "http_get", return_value=response):
            with self.assertRaises(ValueError):
                video.get_yt_info("yqw4Qcv29FY")


class LdJsonTest(TestCase):
    def test_watch_action_wins(self):
        metadata = {"interactionStatistic": [
            {"interactionType": "https://schema.org/LikeAction", "userInteractionCount": 6768},
            {"interactionType": {"@type": "WatchAction"}, "userInteractionCount": 287245}]}
        self.assertEqual(287245, video.ld_json_view_count(metadata))

    def test_interaction_count_field_is_preferred(self):
        metadata = {"interactionCount": "287245", "interactionStatistic": [
            {"interactionType": "WatchAction", "userInteractionCount": 1}]}
        self.assertEqual(287245, video.ld_json_view_count(metadata))

    def test_single_statistic_dict(self):
        metadata = {"interactionStatistic": {"interactionType": "WatchAction",
                                            "userInteractionCount": 123}}
        self.assertEqual(123, video.ld_json_view_count(metadata))

    def test_missing_statistics(self):
        self.assertIsNone(video.ld_json_view_count({}))

    def test_video_object_skips_broken_scripts(self):
        page = ('<html><head><script type="application/ld+json">{not json}</script>'
                '<script type="application/ld+json">'
                '[{"@type":"BreadcrumbList"},{"@type":"VideoObject","uploadDate":"2024-02-23"}]'
                '</script></head></html>')
        soup = BeautifulSoup(page, "html.parser")
        self.assertEqual("2024-02-23", video.ld_json_video_object(soup).get("uploadDate"))

    def test_video_object_absent(self):
        soup = BeautifulSoup("<html><head></head></html>", "html.parser")
        self.assertIsNone(video.ld_json_video_object(soup))


# 非公開 / 删稿的 niconico 视频：watch 页面只剩 404 错误页，改从 nicolog 取数据
NC_PAGE_OK = """<html><head>
<meta property="og:image" content="https://img.cdn.nimg.jp/s/nicovideo/thumbnails/37464090/x" />
</head><body><script>
{"video":{"uploadDate":"2020-09-04T20:00:00+09:00"},"interactionStatistic":{"userInteractionCount":67425}}
</script></body></html>"""

NC_PAGE_GONE = "<html><head><title>ニコニコ動画</title></head><body>error</body></html>"

NICOLOG_VIDEO = SimpleNamespace(identifier="sm16693848", title="【初音ミク】ジェシカ【オリジナル曲】",
                                uploaded=datetime(2012, 1, 14), views=1081622, comments=8843,
                                mylists=15665, uploader="くるりんご", duration="0:03:48",
                                description="", thumbnail="https://tn.smilevideo.jp/smile?i=16693848")


class NicologFallbackTest(TestCase):
    def _response(self, text):
        return SimpleNamespace(text=text)

    def test_public_video_is_not_deleted(self):
        with mock.patch.object(video, "http_get", return_value=self._response(NC_PAGE_OK)), \
             mock.patch.object(video.nicolog, "fetch") as archived:
            result = video.get_nc_info("sm37464090")
        self.assertFalse(result.deleted)
        self.assertEqual(67425, result.views)
        self.assertEqual(datetime(2020, 9, 4), result.uploaded)
        archived.assert_not_called()

    def test_private_video_uses_nicolog(self):
        with mock.patch.object(video, "http_get", return_value=self._response(NC_PAGE_GONE)), \
             mock.patch.object(video.nicolog, "fetch", return_value=NICOLOG_VIDEO) as archived:
            result = video.get_nc_info("sm16693848")
        self.assertTrue(result.deleted)
        self.assertEqual(1081622, result.views)                  # 最后一次记录的播放量
        self.assertEqual(datetime(2012, 1, 14), result.uploaded)  # 投稿日
        self.assertEqual("https://tn.smilevideo.jp/smile?i=16693848", result.thumb_url)
        archived.assert_called_once_with("sm16693848")

    def test_private_video_without_nicolog_record(self):
        with mock.patch.object(video, "http_get", return_value=self._response(NC_PAGE_GONE)), \
             mock.patch.object(video.nicolog, "fetch", return_value=None):
            result = video.get_nc_info("sm99999999")
        self.assertFalse(result.deleted)
        self.assertEqual(0, result.views)

    def test_url_is_parsed_before_fetching(self):
        with mock.patch.object(video, "http_get", return_value=self._response(NC_PAGE_GONE)), \
             mock.patch.object(video.nicolog, "fetch", return_value=NICOLOG_VIDEO) as archived:
            video.get_nc_info("https://www.nicovideo.jp/watch/sm16693848")
        archived.assert_called_once_with("sm16693848")


class GuessVideoSiteTest(TestCase):
    """人声本家只要链接（或裸 ID），先判断它属于哪个站点。"""

    def test_niconico(self):
        for link in ("https://www.nicovideo.jp/watch/sm16693848",
                     "nicovideo.jp/watch/sm16693848", "sm16693848", "so16693848"):
            self.assertEqual(video.VideoSite.NICO_NICO, video.guess_video_site(link), link)

    def test_youtube(self):
        for link in ("https://www.youtube.com/watch?v=TG9IjsxAWUs",
                     "https://youtu.be/TG9IjsxAWUs",
                     "https://www.youtube.com/shorts/TG9IjsxAWUs",
                     "TG9IjsxAWUs"):
            self.assertEqual(video.VideoSite.YOUTUBE, video.guess_video_site(link), link)

    def test_bilibili(self):
        for link in ("https://www.bilibili.com/video/BV1Jv411N7Wn",
                     "bilibili.com/video/BV1Jv411N7Wn", "BV1Jv411N7Wn", "av170001",
                     "https://b23.tv/Zt8UWvS", "b23.tv/Zt8UWvS"):
            self.assertEqual(video.VideoSite.BILIBILI, video.guess_video_site(link), link)

    def test_unknown(self):
        for link in ("", "   ", "https://twitter.com/x0o0x_", "hello"):
            self.assertIsNone(video.guess_video_site(link), link)


class VideoLinkTest(TestCase):
    def test_extracts_identifier_and_url(self):
        nico = video.video_link("https://www.nicovideo.jp/watch/sm27831783")
        self.assertEqual(video.VideoSite.NICO_NICO, nico.site)
        self.assertEqual("sm27831783", nico.identifier)
        self.assertEqual("https://www.nicovideo.jp/watch/sm27831783", nico.url)

        yt = video.video_link("https://youtu.be/40dJS_LC6S8")
        self.assertEqual(video.VideoSite.YOUTUBE, yt.site)
        self.assertEqual("40dJS_LC6S8", yt.identifier)
        self.assertEqual("https://www.youtube.com/watch?v=40dJS_LC6S8", yt.url)

        bb = video.video_link("https://www.bilibili.com/video/BV1Jv411N7Wn?p=1")
        self.assertEqual(video.VideoSite.BILIBILI, bb.site)
        self.assertEqual("BV1Jv411N7Wn", bb.identifier)
        self.assertEqual("https://www.bilibili.com/video/BV1Jv411N7Wn", bb.url)

    def test_bare_ids(self):
        self.assertEqual("sm16693848", video.video_link("sm16693848").identifier)
        self.assertEqual("TG9IjsxAWUs", video.video_link("TG9IjsxAWUs").identifier)
        self.assertEqual("BV1Ex411w7d2", video.video_link("BV1Ex411w7d2").identifier)

    def test_no_network_call(self):
        # 只做本地解析：人声本家不需要播放量与投稿日
        with mock.patch.object(video, "http_get") as http_get:
            link = video.video_link("https://www.nicovideo.jp/watch/sm27831783")
        http_get.assert_not_called()
        self.assertEqual(0, link.views)

    def test_unrecognized_returns_none(self):
        self.assertIsNone(video.video_link("https://twitter.com/x0o0x_"))


class ShortLinkTest(TestCase):
    """B 站短链（b23.tv）：只有短码，得联网跳一次才能拿到 BV 号。

    实测 https://b23.tv/Zt8UWvS → 302，Location 里就是 www.bilibili.com/video/BV1fc386VE6n?…
    """

    LOCATION = ("https://www.bilibili.com/video/BV1fc386VE6n?buvid=XU106&p=1"
                "&share_source=COPY&up_id=3537124697573396")

    def _response(self, location=None, status=302):
        return SimpleNamespace(status_code=status, headers={"Location": location} if location else {})

    def test_short_link_url(self):
        self.assertEqual("https://b23.tv/Zt8UWvS", video.short_link_url("https://b23.tv/Zt8UWvS"))
        self.assertEqual("https://b23.tv/Zt8UWvS", video.short_link_url(" b23.tv/Zt8UWvS "))
        self.assertEqual("https://bili2233.cn/abc", video.short_link_url("bili2233.cn/abc"))

    def test_not_a_short_link(self):
        for link in ("", "   ", "https://www.bilibili.com/video/BV1Jv411N7Wn", "BV1Jv411N7Wn"):
            self.assertIsNone(video.short_link_url(link), link)

    def test_resolve_reads_location_without_following(self):
        with mock.patch.object(video, "http_get", return_value=self._response(self.LOCATION)) as http_get:
            self.assertEqual(self.LOCATION, video.resolve_short_link("https://b23.tv/Zt8UWvS"))
        self.assertEqual("https://b23.tv/Zt8UWvS", http_get.call_args.args[0])
        self.assertFalse(http_get.call_args.kwargs["use_proxy"])     # B 站不需要代理，与 get_bb_info 一致
        self.assertFalse(http_get.call_args.kwargs["allow_redirects"])  # 不跟随，省掉整个视频页

    def test_resolve_without_location(self):
        with mock.patch.object(video, "http_get", return_value=self._response()):
            self.assertIsNone(video.resolve_short_link("https://b23.tv/Zt8UWvS"))

    def test_resolve_swallows_network_error(self):
        with mock.patch.object(video, "http_get", side_effect=OSError("boom")):
            self.assertIsNone(video.resolve_short_link("https://b23.tv/Zt8UWvS"))

    def test_normal_link_never_requests(self):
        with mock.patch.object(video, "http_get") as http_get:
            self.assertIsNone(video.resolve_short_link("BV1Jv411N7Wn"))
        http_get.assert_not_called()

    def test_video_link_follows_short_link(self):
        with mock.patch.object(video, "http_get", return_value=self._response(self.LOCATION)):
            link = video.video_link("https://b23.tv/Zt8UWvS")
        self.assertEqual(video.VideoSite.BILIBILI, link.site)
        self.assertEqual("BV1fc386VE6n", link.identifier)
        self.assertEqual("https://www.bilibili.com/video/BV1fc386VE6n", link.url)

    def test_opus_short_link_is_rejected(self):
        # 图文 / 笔记的短链会跳到 m.bilibili.com/opus/…，不是视频
        opus = "https://m.bilibili.com/opus/967303729760960515?plat_id=5"
        with mock.patch.object(video, "http_get", return_value=self._response(opus)):
            self.assertIsNone(video.video_link("https://b23.tv/4ypW65R"))

    def test_failed_short_link_is_rejected(self):
        with mock.patch.object(video, "http_get", side_effect=OSError("boom")):
            self.assertIsNone(video.video_link("https://b23.tv/Zt8UWvS"))

    def test_bilibili_prompt_resolves_short_link(self):
        with mock.patch.object(video, "prompt_response", return_value="https://b23.tv/Zt8UWvS"), \
             mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "http_get", return_value=self._response(self.LOCATION)), \
             mock.patch.object(video, "video_from_site", return_value="video") as from_site:
            self.assertEqual("video", video.get_video_bilibili())
        self.assertEqual(video.VideoSite.BILIBILI, from_site.call_args.args[0])
        self.assertEqual(self.LOCATION, from_site.call_args.args[1])      # 已换成真实链接


class HumanOriginalTest(TestCase):
    """询问人声本家：先问有没有，再要 niconico / YouTube 与 bilibili 链接（都可留空）。"""

    def test_answers_no(self):
        with mock.patch.object(video, "prompt_choices", return_value=2) as choices, \
             mock.patch.object(video, "prompt_response") as response:
            self.assertIsNone(video.get_human_original())
        self.assertEqual(1, choices.call_count)
        response.assert_not_called()

    def test_collects_both_links(self):
        answers = iter(["https://www.nicovideo.jp/watch/sm27831783",
                        "https://www.bilibili.com/video/BV1Jv411N7Wn"])
        with mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "prompt_response", side_effect=lambda *a, **k: next(answers)):
            human = video.get_human_original()
        self.assertEqual("sm27831783", human.video.identifier)
        self.assertEqual(video.VideoSite.NICO_NICO, human.video.site)
        self.assertEqual("BV1Jv411N7Wn", human.bilibili.identifier)

    def test_youtube_link_and_missing_bilibili(self):
        answers = iter(["https://www.youtube.com/watch?v=TG9IjsxAWUs", ""])
        with mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "prompt_response", side_effect=lambda *a, **k: next(answers)):
            human = video.get_human_original()
        self.assertEqual(video.VideoSite.YOUTUBE, human.video.site)
        self.assertIsNone(human.bilibili)

    def test_both_empty_means_no_human_original(self):
        with mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "prompt_response", return_value=""):
            self.assertIsNone(video.get_human_original())

    def test_wrong_site_is_asked_again(self):
        # 第一个问题只认 niconico / YouTube：填了 B 站链接要重问
        answers = iter(["BV1Jv411N7Wn", "sm27831783", ""])
        with mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "prompt_response", side_effect=lambda *a, **k: next(answers)), \
             mock.patch("builtins.print") as printed:
            human = video.get_human_original()
        self.assertEqual("sm27831783", human.video.identifier)
        self.assertIsNone(human.bilibili)
        self.assertTrue(printed.called)

    def test_bilibili_question_rejects_niconico(self):
        answers = iter(["", "sm27831783", "BV1Jv411N7Wn"])
        with mock.patch.object(video, "prompt_choices", return_value=1), \
             mock.patch.object(video, "prompt_response", side_effect=lambda *a, **k: next(answers)), \
             mock.patch("builtins.print"):
            human = video.get_human_original()
        self.assertIsNone(human.video)
        self.assertEqual("BV1Jv411N7Wn", human.bilibili.identifier)
