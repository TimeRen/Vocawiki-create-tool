"""截图不该被「取数失败」连坐（用户 2026-10-23：该走 B 站截图却没截）。

`available()` 里的 60 秒冷战是给「一页页等着 JSON」用的：前面只要有一次取数失败
（VocaDB 没等到 / AtWiki 挑战没过），紧接着的封面截图就整批被跳过。
两件事的代价不一样 —— 截图有自己的开关（`screenshot_available()`）。
"""
import os
import time
from pathlib import Path
from unittest import TestCase, mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"          # 跑测时绝不能真起浏览器

from utils import browser_fetch                        # noqa: E402


class ScreenshotGateTest(TestCase):
    def setUp(self):
        saved = (browser_fetch._failed_at,)
        self.addCleanup(setattr, browser_fetch, "_failed_at", saved[0])
        browser_fetch._failed_at = 0.0

    @staticmethod
    def _browser_on():
        """临时打开「能借浏览器」这条路（`DISABLE_ENV` 换成一个没设过的变量名）。"""
        return mock.patch.object(browser_fetch, "DISABLE_ENV", "VOCAWIKI_TEST_NO_SUCH_ENV")

    def test_a_failed_fetch_does_not_block_screenshots(self):
        browser_fetch._failed_at = time.time()          # 刚才取数失败过
        with self._browser_on(), \
             mock.patch.object(browser_fetch, "find_browser", return_value="chrome.exe"):
            self.assertFalse(browser_fetch.available(), "取数照旧先歇一会儿")
            self.assertTrue(browser_fetch.screenshot_available(), "截图不该被连坐")

    def test_screenshots_still_respect_the_off_switch(self):
        with mock.patch.object(browser_fetch, "find_browser", return_value="chrome.exe"):
            self.assertFalse(browser_fetch.screenshot_available())     # 环境变量已置位

    def test_screenshots_need_a_browser(self):
        with self._browser_on(), mock.patch.object(browser_fetch, "find_browser", return_value=None):
            self.assertFalse(browser_fetch.screenshot_available())

    def test_screenshot_takes_the_allowed_video_pages(self):
        session = mock.MagicMock()
        session.screenshot_page.return_value.screenshot.return_value = b"\x89PNG\r\n\x1a\n"
        with self._browser_on(), \
             mock.patch.object(browser_fetch, "find_browser", return_value="chrome.exe"), \
             mock.patch.object(browser_fetch, "_get_session", return_value=session):
            data = browser_fetch.screenshot("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data)
        self.assertEqual(0.0, browser_fetch._failed_at)

    def test_a_broken_browser_is_remembered(self):
        """浏览器根本起不来时记一次失败：后面每个候选页面别再各等满 READY_TIMEOUT。"""
        with self._browser_on(), \
             mock.patch.object(browser_fetch, "find_browser", return_value="chrome.exe"), \
             mock.patch.object(browser_fetch, "_get_session", return_value=None):
            self.assertIsNone(browser_fetch.screenshot("https://www.bilibili.com/video/BV1xx411c7mD"))
        self.assertGreater(browser_fetch._failed_at, 0.0)


class ScreenshotPageTest(TestCase):
    """截图用**独立标签页**：取数的导航不能再把要拍的那一页顶掉（用户 2026-11-02 报）。

    那天 `logs.txt` 里，取数把同一个页面连续导航到 atwiki（`/search` 卡了 60 秒），
    封面那一步要么拍不到、要么窗口里停着 atwiki —— 本该是 B 站页面。
    """

    def setUp(self):
        self.addCleanup(setattr, browser_fetch, "_failed_at", browser_fetch._failed_at)
        browser_fetch._failed_at = 0.0

    @staticmethod
    def _browser_on():
        return mock.patch.object(browser_fetch, "DISABLE_ENV", "VOCAWIKI_TEST_NO_SUCH_ENV")

    def test_the_screenshot_goes_to_its_own_page(self):
        session = mock.MagicMock()
        session.screenshot_page.return_value.screenshot.return_value = b"\x89PNG"
        with self._browser_on(), \
             mock.patch.object(browser_fetch, "find_browser", return_value="chrome.exe"), \
             mock.patch.object(browser_fetch, "_get_session", return_value=session):
            browser_fetch.screenshot("https://www.bilibili.com/video/BV1xx411c7mD")
        session.screenshot_page.assert_called_once(), "截图必须先拿截图页"
        session.page.fetch.assert_not_called()

    def test_each_page_keeps_its_own_target_url(self):
        """两个页面各记各的地址：取数导航到 atwiki 不会把截图页的 B 站地址顶掉。"""
        fetch_page = browser_fetch._Page(ws=object(), name="取数")
        shot_page = browser_fetch._Page(ws=object(), name="截图")
        for page in (fetch_page, shot_page):
            page.call = mock.MagicMock()
            page.location = mock.MagicMock(return_value="")
        fetch_page.navigate("https://w.atwiki.jp/hmiku/pages/1.html")
        shot_page.navigate("https://www.bilibili.com/video/BV1")
        self.assertIn("atwiki", fetch_page.target_url)
        self.assertIn("bilibili", shot_page.target_url)
        shot_page.call.reset_mock()
        shot_page.location = mock.MagicMock(side_effect=lambda: shot_page.target_url)
        shot_page.navigate("https://www.bilibili.com/video/BV1")     # 还在这一页：不重复导航
        shot_page.call.assert_not_called()

    def test_park_sends_both_pages_to_a_blank_page(self):
        session = browser_fetch._Session("chrome.exe", Path("."))
        session.page = mock.MagicMock()
        session._shot_page = mock.MagicMock()
        session.park()
        session.page.park.assert_called_once()
        session._shot_page.park.assert_called_once()

    def test_park_does_nothing_without_a_session(self):
        with mock.patch.object(browser_fetch, "_session", None):
            browser_fetch.park()                        # 不该抛异常


class MuteAndDanmakuTest(TestCase):
    """截图前要静音 + 关弹幕（用户 2026-11-02 要求）。"""

    def _page(self):
        page = browser_fetch._Page(ws=object(), name="截图")
        page.call = mock.MagicMock()
        page.evaluate = mock.MagicMock(return_value="")
        return page

    def test_mute_turns_the_page_and_the_media_off(self):
        page = self._page()
        page.mute()
        page.call.assert_any_call("Page.setMuted", {"muted": True}, timeout=5)
        self.assertIn("muted = true", page.evaluate.call_args_list[-1].args[0])

    def test_hiding_the_danmaku_layer_injects_a_stylesheet(self):
        page = self._page()
        page.hide_overlays()
        script = page.evaluate.call_args_list[-1].args[0]
        self.assertIn("danmaku", script)
        self.assertIn("bpx-player-dm-root", script)
        self.assertIn("display: none", script)

    def test_screenshot_mutes_and_hides_before_capturing(self):
        page = self._page()
        page.navigate = mock.MagicMock()
        page.challenge_page = mock.MagicMock(return_value=False)

        def evaluate(expression, timeout=10):
            if expression is browser_fetch.BODY_TEXT_JS:
                return "视频标题"
            if expression is browser_fetch.COVER_RECT_JS:
                return '{"x": 0, "y": 0, "width": 100, "height": 100, ' \
                       '"naturalWidth": 100, "naturalHeight": 100}'
            return "1"

        page.evaluate = mock.MagicMock(side_effect=evaluate)
        page.call = mock.MagicMock(return_value={"data": "UE5H"})
        page.screenshot("https://www.bilibili.com/video/BV1xx411c7mD", timeout=1)
        calls = [item.args[0] for item in page.call.call_args_list]
        self.assertIn("Page.setMuted", calls)
        self.assertIn("Page.captureScreenshot", calls)
        scripts = [item.args[0] for item in page.evaluate.call_args_list]
        self.assertTrue(any("danmaku" in script for script in scripts),
                        "截图前必须把弹幕藏掉")

