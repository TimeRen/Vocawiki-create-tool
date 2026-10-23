"""截图不该被「取数失败」连坐（用户 2026-10-23：该走 B 站截图却没截）。

`available()` 里的 60 秒冷战是给「一页页等着 JSON」用的：前面只要有一次取数失败
（VocaDB 没等到 / AtWiki 挑战没过），紧接着的封面截图就整批被跳过。
两件事的代价不一样 —— 截图有自己的开关（`screenshot_available()`）。
"""
import os
import time
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
        session = mock.Mock()
        session.screenshot.return_value = b"\x89PNG\r\n\x1a\n"
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
