"""右下角的下载进度环 + 带进度的后台任务（用户 2026-10-24 要求）。

需求原话：「在更新版本时在右下角显示一个进度环用以显示下载进度」。
"""
import os
from unittest import TestCase, mock

from PyQt5 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils.ui.progress_ring import (RING_MARGIN, RING_SIZE, ProgressRing)   # noqa: E402
from utils.ui.workers import ProgressWorker                               # noqa: E402


class ProgressRingTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.parent = QtWidgets.QWidget()
        self.parent.resize(400, 300)
        self.addCleanup(self.parent.deleteLater)
        self.ring = ProgressRing(self.parent)

    def test_shows_the_downloaded_ratio(self):
        self.ring.set_progress(25, 100)
        self.assertTrue(self.ring.isVisibleTo(self.parent))
        self.assertAlmostEqual(0.25, self.ring.progress_ratio())
        self.ring.set_progress(100, 100)
        self.assertAlmostEqual(1.0, self.ring.progress_ratio())

    def test_ratio_is_clamped(self):
        self.ring.set_progress(300, 100)
        self.assertAlmostEqual(1.0, self.ring.progress_ratio())
        self.ring.set_progress(0, 0)                 # 不知道总量
        self.assertAlmostEqual(0.0, self.ring.progress_ratio())
        self.assertTrue(self.ring._timer.isActive(), "不知道总量时自己转圈")

    def test_finish_hides_the_ring(self):
        self.ring.set_progress(10, 100)
        self.ring.finish()
        self.assertFalse(self.ring.isVisibleTo(self.parent))
        self.assertFalse(self.ring._timer.isActive())

    def test_it_sits_in_the_bottom_right_corner(self):
        self.ring.set_progress(1, 2)
        self.ring.place()
        self.assertEqual(400 - RING_SIZE - RING_MARGIN, self.ring.x())
        self.assertEqual(300 - RING_SIZE - RING_MARGIN, self.ring.y())

    def test_it_does_not_steal_clicks(self):
        from PyQt5 import QtCore
        self.assertTrue(self.ring.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))

    def test_it_paints_without_errors(self):
        """真画一遍（offscreen）：圆环 + 百分比文字都要能画出来。"""
        from PyQt5 import QtGui
        self.ring.set_progress(42, 100)
        self.ring.resize(RING_SIZE, RING_SIZE)
        pixmap = self.ring.grab()
        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.width(), 0)
        image = pixmap.toImage()
        center = QtGui.QColor(image.pixel(RING_SIZE // 2, RING_SIZE // 2))
        self.assertTrue(center.isValid())


class ProgressWorkerTest(TestCase):
    """`ProgressWorker` 把函数里的 `report()` 变成跨线程的 `progress` 信号。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_the_function_receives_a_report_callable(self):
        seen = []

        def task(total, report=None):
            report(1, total)
            report(total, total)
            return "done"

        worker = ProgressWorker(task, 4)
        finished = []
        worker.done.connect(finished.append)
        worker.progress.connect(lambda done, total: seen.append((done, total)))
        worker.run()                                  # 直接跑，不用真的起线程
        self.assertEqual([(1, 4), (4, 4)], seen)
        self.assertEqual(["done"], finished)

    def test_errors_become_the_usual_failure_dict(self):
        def task(report=None):
            raise RuntimeError("网络断了")

        worker = ProgressWorker(task)
        finished = []
        worker.done.connect(finished.append)
        with mock.patch("utils.ui.workers.logging.error"):
            worker.run()
        self.assertEqual(1, len(finished))
        self.assertFalse(finished[0]["ok"])
        self.assertIn("网络断了", finished[0]["error"])
