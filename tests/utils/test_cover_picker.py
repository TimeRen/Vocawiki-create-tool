"""「选择封面」对话框（用户 2026-11-02：只截一张常常不适合当封面，要几张让我挑）。"""
import os
import tempfile
from pathlib import Path
from unittest import TestCase, mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets                                               # noqa: E402
from PIL import Image                                                     # noqa: E402

from utils.ui import cover_picker                                         # noqa: E402


def _write(path: Path, size=(320, 180)) -> Path:
    Image.new("RGB", size, "#39c5bb").save(path, "PNG")
    return path


class CoverPickerDialogTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.first = _write(Path(self.folder.name) / "a.png")
        self.second = _write(Path(self.folder.name) / "b.png", (640, 360))
        self.candidates = [(self.first, "页面声明的封面（原图）"),
                           (self.second, "视频画面 35%")]

    def test_lists_every_candidate(self):
        dialog = cover_picker.CoverPickerDialog(self.candidates)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(2, dialog.list.count())
        self.assertIn("页面声明的封面", dialog.list.item(0).text())
        self.assertIn("视频画面 35%", dialog.list.item(1).text())
        self.assertEqual(0, dialog.list.currentRow(), "默认选中第一张")

    def test_ok_returns_the_checked_one(self):
        dialog = cover_picker.CoverPickerDialog(self.candidates)
        self.addCleanup(dialog.deleteLater)
        dialog.list.setCurrentRow(1)
        dialog._on_ok()
        self.assertEqual(self.second, dialog.selected())

    def test_double_click_picks_that_one(self):
        dialog = cover_picker.CoverPickerDialog(self.candidates)
        self.addCleanup(dialog.deleteLater)
        dialog._pick(self.first)
        self.assertEqual(self.first, dialog.selected())

    def test_choose_cover_returns_none_when_rejected(self):
        """「都不合适（保持原图）」→ 返回 None（流程保留原来的封面）。"""
        with mock.patch.object(cover_picker.CoverPickerDialog, "exec_",
                               return_value=QtWidgets.QDialog.Rejected):
            self.assertIsNone(cover_picker.choose_cover(self.candidates))

    def test_choose_cover_returns_the_picked_path(self):
        with mock.patch.object(cover_picker.CoverPickerDialog, "exec_",
                               return_value=QtWidgets.QDialog.Accepted), \
             mock.patch.object(cover_picker.CoverPickerDialog, "selected",
                               return_value=self.first):
            self.assertEqual(self.first, cover_picker.choose_cover(self.candidates))

    def test_no_candidates_means_no_dialog(self):
        self.assertIsNone(cover_picker.choose_cover([]))
