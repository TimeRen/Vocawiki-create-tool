"""`{{bilibiliVideo}}` 的封面 / 标题（`utils/bilibili.py`）。

桌面预览的 QtWebEngine 没有 H.264，放不了 bilibili 的播放器（用户 2026-10-27 截图：
iframe 里只有「您当前的浏览器不支持 HTML5 播放器」）。所以预览改成用接口取来的封面
画一张「封面 + 播放按钮」的卡片 —— 这个模块负责取封面，取不到就安静地返回空。
"""
from unittest import TestCase
from unittest import mock

from utils import bilibili

RENDERED = (
    '<div class="bilibili-video-container" data-id="BV1M3411C79b" data-page="1" '
    'data-pagename="" data-title="" data-autoplay="false" data-subtitle="false">'
    '<div class="bilibili-title" style="text-align: center;">'
    '<span class="bilibili-widescreen">宽屏模式</span></div>'
    '<div class="bilibili-iframe-container"></div></div>'
)

PAYLOAD = {"code": 0, "message": "OK", "data": {
    "title": "binary biotope / mawari feat. 可不(KAFU)",
    "pic": "http://i0.hdslb.com/bfs/archive/a4938d27f32afd05e3cb0a96c037219bc2ff8913.jpg",
    "owner": {"name": "稀饭丶Dawn"},
    "pages": [{"page": 1}],
}}


def _response(payload):
    response = mock.Mock()
    response.json.return_value = payload
    return response


class VideoIdsTest(TestCase):
    def test_reads_data_id_from_rendered_containers(self):
        html = RENDERED + RENDERED.replace("BV1M3411C79b", "av170001")
        self.assertEqual(["BV1M3411C79b", "av170001"], bilibili.video_ids(html))

    def test_keeps_order_and_drops_duplicates(self):
        html = RENDERED + RENDERED + RENDERED.replace("BV1M3411C79b", "BV1oh411L797")
        self.assertEqual(["BV1M3411C79b", "BV1oh411L797"], bilibili.video_ids(html))

    def test_empty_html_has_no_videos(self):
        self.assertEqual([], bilibili.video_ids(""))
        self.assertEqual([], bilibili.video_ids('<div class="bilibili-video-container"></div>'))


class VideoInfoTest(TestCase):
    def setUp(self):
        bilibili.reset_cache()

    def tearDown(self):
        bilibili.reset_cache()

    def test_reads_cover_title_and_owner(self):
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response(PAYLOAD)) as http_get:
            info = bilibili.video_info("BV1M3411C79b")
        self.assertEqual("binary biotope / mawari feat. 可不(KAFU)", info["title"])
        self.assertEqual("稀饭丶Dawn", info["owner"])
        # 站上是 http 的封面，https 页面里会被当混合内容拦掉
        self.assertTrue(info["pic"].startswith("https://i0.hdslb.com/"))
        self.assertEqual("https://www.bilibili.com/video/BV1M3411C79b", info["url"])
        self.assertEqual({"bvid": "BV1M3411C79b"}, http_get.call_args.kwargs["params"])

    def test_av_number_uses_aid(self):
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response(PAYLOAD)) as http_get:
            bilibili.video_info("av170001")
        self.assertEqual({"aid": "170001"}, http_get.call_args.kwargs["params"])

    def test_success_is_cached(self):
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response(PAYLOAD)) as http_get:
            bilibili.video_info("BV1M3411C79b")
            bilibili.video_info("BV1M3411C79b")
        self.assertEqual(1, http_get.call_count)

    def test_failures_are_cached_for_a_while(self):
        """预览每改一个字就刷一次，取不到的视频别每次都白等 6 秒。"""
        with mock.patch.object(bilibili, "http_get",
                               side_effect=OSError("boom")) as http_get:
            self.assertIsNone(bilibili.video_info("BV1M3411C79b"))
            self.assertIsNone(bilibili.video_info("BV1M3411C79b"))
        self.assertEqual(1, http_get.call_count)

    def test_non_zero_code_returns_none(self):
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response({"code": -404, "message": "啥都木有"})):
            self.assertIsNone(bilibili.video_info("BV1M3411C79b"))

    def test_unknown_id_is_not_requested(self):
        with mock.patch.object(bilibili, "http_get") as http_get:
            self.assertIsNone(bilibili.video_info("不是视频号"))
        http_get.assert_not_called()

    def test_video_cards_returns_every_mounted_video(self):
        html = RENDERED + '<div class="bilibili-video-container" data-id="BV1oh411L797"></div>'
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response(PAYLOAD)):
            cards = bilibili.video_cards(html)
        self.assertEqual(["BV1M3411C79b", "BV1oh411L797"], list(cards))

    def test_video_cards_skips_videos_that_could_not_be_read(self):
        html = RENDERED + '<div class="bilibili-video-container" data-id="BV1oh411L797"></div>'

        def fake_get(_url, **_kwargs):
            raise OSError("boom")

        with mock.patch.object(bilibili, "http_get", side_effect=fake_get):
            self.assertEqual({}, bilibili.video_cards(html))

    def test_video_cards_limits_the_number_of_requests(self):
        ids = [f"BV1oh411L79{i}" for i in range(bilibili.MAX_VIDEOS + 2)]
        html = "".join(f'<div class="bilibili-video-container" data-id="{i}"></div>'
                       for i in ids)
        with mock.patch.object(bilibili, "http_get",
                               return_value=_response(PAYLOAD)) as http_get:
            bilibili.video_cards(html)
        self.assertEqual(bilibili.MAX_VIDEOS, http_get.call_count)
