"""`utils/rate_limit.py` 的测试（不联网、不真睡）。

速率墙是**平均间隔**：「每分钟 N 次」= 两次提交之间至少隔 60/N 秒（开头第一笔立刻放行），
所以不会出现「先连发 N 笔再罚站一分钟」（那是 2026-09 之前的旧实现，用户报过）。
这里用假时钟（`time.monotonic` 与 `rate_limit._sleep` 都换掉）来测，跑得飞快。
"""
import unittest
from unittest import mock

from config.config import get_config
from tests import real_wait_for_slot
from utils import rate_limit


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        # tests/__init__.py 里把它换成了空操作（免得别的用例真睡），这里换回真身
        self._wait = rate_limit.wait_for_slot
        self._sleep = rate_limit._sleep
        rate_limit.wait_for_slot = real_wait_for_slot
        self.clock = [1000.0]
        self.slept = []
        rate_limit._sleep = self._fake_sleep
        rate_limit.reset()
        clock_patcher = mock.patch.object(rate_limit.time, "monotonic",
                                          side_effect=lambda: self.clock[0])
        clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        rate_limit.wait_for_slot = self._wait
        rate_limit._sleep = self._sleep
        rate_limit.reset()

    def _fake_sleep(self, seconds: float) -> None:
        """假睡：记一笔并把假时钟往前拨。"""
        self.slept.append(seconds)
        self.clock[0] += seconds

    def test_default_limit_is_three_edits_per_minute(self):
        self.assertEqual(3, rate_limit.DEFAULT_EDITS_PER_MINUTE)
        self.assertEqual(3, rate_limit.configured_limit())

    def test_limit_comes_from_wiki_config(self):
        wiki = get_config().wiki
        with mock.patch.object(wiki, "edits_per_minute", 7):
            self.assertEqual(7, rate_limit.configured_limit())
        with mock.patch.object(wiki, "edits_per_minute", 0):
            self.assertEqual(0, rate_limit.configured_limit())
        with mock.patch.object(wiki, "edits_per_minute", "12"):     # 手写 config.yaml 写成字符串也认
            self.assertEqual(12, rate_limit.configured_limit())
        with mock.patch.object(wiki, "edits_per_minute", -5):
            self.assertEqual(0, rate_limit.configured_limit())      # 负数按不限制处理

    def test_first_edit_goes_straight_through(self):
        """第一笔立刻放行（没有上一笔可参照）。"""
        self.assertEqual(0.0, rate_limit.wait_for_slot())
        self.assertEqual([], self.slept)
        self.assertEqual(1, rate_limit.recorded())

    def test_interval_is_sixty_over_n(self):
        """间隔 = 60/N 秒（再留半秒余量）：3 次/分钟 → 每 20.5 秒一次。"""
        self.assertAlmostEqual(20.5, rate_limit.interval_seconds(3), places=3)
        self.assertAlmostEqual(30.5, rate_limit.interval_seconds(2), places=3)
        self.assertAlmostEqual(60.5, rate_limit.interval_seconds(1), places=3)
        self.assertAlmostEqual(6.5, rate_limit.interval_seconds(10), places=3)
        self.assertEqual(0.0, rate_limit.interval_seconds(0))       # 0 = 不限制

    def test_second_edit_waits_instead_of_bursting(self):
        """关键回归：不是「先连发 N 笔」—— 第二笔就得等都上一笔。"""
        self.assertEqual(0.0, rate_limit.wait_for_slot())
        second = rate_limit.wait_for_slot()
        self.assertAlmostEqual(20.5, second, places=3)
        third = rate_limit.wait_for_slot()
        self.assertAlmostEqual(20.5, third, places=3)
        self.assertEqual([20.5, 20.5], [round(item, 3) for item in self.slept])
        self.assertEqual(3, rate_limit.recorded())

    def test_edits_are_evenly_spaced(self):
        """连着提交 5 笔：提交时刻均匀铺开，间隔一模一样。"""
        times = []
        for _ in range(5):
            rate_limit.wait_for_slot()
            times.append(self.clock[0] - 1000.0)        # 相对假时钟起点（1000.0）
        self.assertEqual([0.0, 20.5, 41.0, 61.5, 82.0], [round(t, 3) for t in times])

    def test_no_sixty_second_window_exceeds_the_limit(self):
        """硬指标：任意 60 秒里最多 N 次（这才是站点要的东西）。"""
        times = []
        for _ in range(20):
            rate_limit.wait_for_slot()
            times.append(self.clock[0])
        for start in times:
            in_window = [t for t in times if start <= t < start + 60.0]
            self.assertLessEqual(len(in_window), 3, f"{start} 起一分钟内有 {len(in_window)} 次")

    def test_after_a_long_idle_the_next_edit_is_immediate(self):
        rate_limit.wait_for_slot()
        self.clock[0] += 61.0                            # 闲置一分多钟 → 下次不用等
        self.assertEqual(0.0, rate_limit.wait_for_slot())
        self.assertEqual([], self.slept)
        self.assertEqual(1, rate_limit.recorded())

    def test_zero_means_no_limit(self):
        with mock.patch.object(rate_limit, "configured_limit", return_value=0):
            self.assertEqual([0.0] * 10, [rate_limit.wait_for_slot() for _ in range(10)])
        self.assertEqual([], self.slept)
        self.assertEqual(0, rate_limit.recorded())           # 不限制就一次也不记

    def test_limit_change_takes_effect_immediately(self):
        """在「设置」页改完保存后就该生效（每次都现读配置，不用重启）。"""
        with mock.patch.object(rate_limit, "configured_limit", return_value=1):
            rate_limit.wait_for_slot()
            self.assertAlmostEqual(60.5, rate_limit.wait_for_slot(), places=3)
        with mock.patch.object(rate_limit, "configured_limit", return_value=0):
            self.assertEqual(0.0, rate_limit.wait_for_slot())

    def test_reset_clears_the_window(self):
        rate_limit.wait_for_slot()
        rate_limit.wait_for_slot()
        rate_limit.reset()
        self.assertEqual(0, rate_limit.recorded())
        self.assertEqual(0.0, rate_limit.wait_for_slot())


class WritePathsUseTheWallTest(unittest.TestCase):
    """写操作（编辑 / 移动 / 上传）都要先排队；读操作不排。"""

    def setUp(self):
        self.calls = []
        patcher = mock.patch("utils.rate_limit.wait_for_slot",
                             side_effect=lambda: self.calls.append(1) or 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_edit_page_asks_for_a_slot(self):
        from utils import wiki_api
        with mock.patch.object(wiki_api.login, "is_logged_in", return_value=True), \
             mock.patch.object(wiki_api.login, "get_csrf_token", return_value="t"), \
             mock.patch.object(wiki_api, "_post_with_retry",
                               return_value={"edit": {"result": "Success", "title": "A"}}):
            result = wiki_api.edit_page("A", "正文")
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(self.calls))

    def test_move_page_asks_for_a_slot(self):
        from utils import wiki_api
        with mock.patch.object(wiki_api.login, "is_logged_in", return_value=True), \
             mock.patch.object(wiki_api.login, "get_csrf_token", return_value="t"), \
             mock.patch.object(wiki_api, "_post_with_retry", return_value={"move": {}}):
            result = wiki_api.move_page("A", "B")
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(self.calls))

    def test_login_required_does_not_take_a_slot(self):
        from utils import wiki_api
        with mock.patch.object(wiki_api.login, "is_logged_in", return_value=False):
            self.assertFalse(wiki_api.edit_page("A", "正文")["ok"])
            self.assertFalse(wiki_api.move_page("A", "B")["ok"])
        self.assertEqual([], self.calls)

    def test_upload_asks_for_a_slot(self):
        from utils import upload
        with mock.patch.object(upload.login, "is_logged_in", return_value=True), \
             mock.patch.object(upload.login, "get_csrf_token", return_value="t"), \
             mock.patch.object(upload.login, "get_session") as session:
            session.return_value.post.return_value.json.return_value = {
                "upload": {"result": "Success"}}
            session.return_value.post.return_value.raise_for_status.return_value = None
            result = upload.upload_image(__file__, "封面.png", "测试曲")
        self.assertTrue(result["ok"], result)
        self.assertEqual(1, len(self.calls))


if __name__ == "__main__":
    unittest.main()
