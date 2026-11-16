"""榜单页那一套界面的单测（侧栏「刊」、榜单表格、生成按钮）。

单独一个文件（与 `test_vocalist_ui.py` 同理）：`test_ui.py` 已经很大，这一组只碰榜单页。
"""
import os
from unittest import TestCase, mock

from PyQt5 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_WEBENGINE"] = "1"

from utils.ui.window import _Bridge as _UiBridge                               # noqa: E402
from utils.ui.window import install_exception_guard                            # noqa: E402

_guard_bridge = _UiBridge()
GUARDED_ERRORS = []
_guard_bridge.crashed.connect(lambda message, trace: GUARDED_ERRORS.append(message))
install_exception_guard(_guard_bridge)

from utils import chart_editor as ce                                            # noqa: E402
from utils import chart_nico                                                    # noqa: E402
from utils.ui.chart_panel import (ATTRS, BADGE_COLUMN, COLUMNS, NOTE_COLUMN,     # noqa: E402
                                 OTHER_COLUMN, SECTION_COLUMN, ChartPanel)


def _work():
    return ce.ChartWork(
        spec=chart_nico.CHARTS["utau"], index=52, video_id="sm8763104", uploader="あーさん",
        publish="2009年11月10日 00:06", start="2009年11月01日 00:00",
        end="2009年11月08日 00:00", rule="2008", description="○集計期間：2009年11月1日0時",
        bilibili="BV1GZ4y1x7Zo", frames=38, source="站上已有页面",
        rows=[ce.ChartRow(rank="1", title="Lilium", vocalist="重音Teto", producer="むのーP",
                          video_id="sm8673630", time="2009-11-01 02:39", score="12759",
                          view="5358", comment="146", mylist="363",
                          other={"评论权重": "0.97"}),
              ce.ChartRow(rank="2", title="白花", vocalist="重音Teto", score="1223")])


class BasePanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面抛出过异常：{GUARDED_ERRORS}")


class ChartPanelTest(BasePanelTest):
    """「榜单」页：表格、每一格可改、加行删行恢复、预览与生成。"""

    def setUp(self):
        self.panel = ChartPanel()
        self.work = _work()
        self.panel.start({"work": self.work})

    def tearDown(self):
        self.panel.deleteLater()
        super().tearDown()

    def test_columns_and_rows(self):
        headers = [self.panel.table.horizontalHeaderItem(index).text()
                   for index in range(self.panel.table.columnCount())]
        self.assertEqual(list(COLUMNS), headers)
        self.assertEqual(2, self.panel.table.rowCount())
        self.assertEqual("1", self.panel.table.item(0, 0).text())
        self.assertEqual("Lilium", self.panel.table.item(0, 1).text())
        self.assertEqual("评论权重 = 0.97", self.panel.table.item(0, OTHER_COLUMN).text())
        self.assertIn("周刊UTAU排行榜52", self.panel.title_label.text())
        self.assertIn("2个名次", self.panel.summary_label.text())

    def test_section_and_badge_are_dropdowns(self):
        section = self.panel.table.cellWidget(0, SECTION_COLUMN)
        badge = self.panel.table.cellWidget(0, BADGE_COLUMN)
        self.assertEqual(["主榜", "番外"], [section.itemText(index)
                                          for index in range(section.count())])
        self.assertEqual(["（无）", "PICKUP", "TOPIC", "上周冠军"],
                         [badge.itemText(index) for index in range(badge.count())])
        self.assertEqual("主榜", section.currentText())
        self.assertEqual("（无）", badge.currentText())

    def test_header_fields_are_filled(self):
        self.assertEqual("2009年11月10日 00:06", self.panel.fields["publish"].text())
        self.assertEqual("2008", self.panel.fields["rule"].text())
        self.assertEqual("BV1GZ4y1x7Zo", self.panel.fields["bilibili"].text())

    def test_preview_shows_the_wikitext(self):
        text = self.panel.preview.toPlainText()
        self.assertIn("{{UTAU Ranking", text)
        self.assertIn("|index = 52", text)
        self.assertIn("|id = 8673630", text)
        self.assertIn("[[Category:周刊VOCAL Character & UTAU排行榜]]", text)
        self.assertIn("2个名次", self.panel.count_label.text())

    def test_editing_a_cell_reaches_the_preview(self):
        self.panel.table.item(1, 6).setText("9999")          # 得点
        self.assertEqual("9999", self.work.rows[1].score)
        self.assertIn("|得点 = 9999", self.panel.preview.toPlainText())

    def test_editing_other_params_reaches_the_model(self):
        self.panel.table.item(1, OTHER_COLUMN).setText("翻唱 = 1")
        self.assertEqual({"翻唱": "1"}, self.work.rows[1].other)
        self.assertIn("|翻唱 = 1", self.panel.preview.toPlainText())

    def test_picking_a_badge_adds_color_and_bottom_column(self):
        box = self.panel.table.cellWidget(0, BADGE_COLUMN)
        box.setCurrentText("TOPIC")
        self.assertEqual("TOPIC", self.work.rows[0].badge)
        text = self.panel.preview.toPlainText()
        self.assertIn("|color = #01BB00", text)
        self.assertIn("|bottom-column = {{color|#01BB00|T O P I C}}", text)

    def test_moving_a_row_to_the_extra_section_renders_a_heading(self):
        box = self.panel.table.cellWidget(1, SECTION_COLUMN)
        box.setCurrentText("番外")
        self.assertEqual("番外", self.work.rows[1].section)
        self.assertIn("=== UTAU番外榜 ===", self.panel.preview.toPlainText())

    def test_editing_the_cover_column_reaches_the_preview(self):
        self.panel.table.item(0, ATTRS.index("cover")).setText("1")
        self.assertEqual("1", self.work.rows[0].cover)
        self.assertIn("|翻唱 = 1", self.panel.preview.toPlainText())

    def test_note_column_is_read_only(self):
        from PyQt5 import QtCore

        flags = self.panel.table.item(0, NOTE_COLUMN).flags()
        self.assertFalse(bool(flags & QtCore.Qt.ItemIsEditable))

    def test_typed_title_pulls_the_remembered_translation(self):
        """手填的曲名命中 user_names.yaml → 自动补「条目」。"""
        from utils import user_dict

        user_dict._cache = {"vocalists": {}, "titles": {"白花": "白花(usimitsudoki)"}}
        try:
            self.panel.table.item(0, ATTRS.index("title")).setText("白花")
            self.assertEqual("白花(usimitsudoki)", self.work.rows[0].entry)
            self.assertEqual("白花(usimitsudoki)",
                             self.panel.table.item(0, ATTRS.index("entry")).text())
        finally:
            user_dict._cache = None

    def test_header_edit_changes_the_rule_and_navbox(self):
        self.panel.fields["rule"].setText("2023")
        self.panel._on_header_edited()
        self.assertIn("{{周刊VOCAL Character & UTAU RANKING|2023}}",
                      self.panel.preview.toPlainText())

    def test_add_remove_and_restore(self):
        self.panel._add_row()
        self.assertEqual(3, self.panel.table.rowCount())
        self.assertEqual(3, len(self.work.rows))
        self.panel.table.selectRow(2)
        self.panel._remove_rows()
        self.assertEqual(2, self.panel.table.rowCount())
        self.panel._restore()
        self.assertEqual(2, len(self.work.rows))
        self.assertEqual("Lilium", self.work.rows[0].title)

    def test_empty_added_row_is_not_rendered(self):
        self.panel._add_row()
        self.assertNotIn("|本周 = 3\n}}", self.panel.preview.toPlainText())

    def test_save_emits_the_work(self):
        saved = []
        self.panel.saved.connect(saved.append)
        self.panel.save_button.click()
        self.assertEqual([self.work], saved)

    def test_cancel_emits(self):
        cancelled = []
        self.panel.cancelled.connect(lambda: cancelled.append(True))
        self.panel.cancel_button.click()
        self.assertEqual([True], cancelled)

    def test_enrich_runs_in_a_worker_and_writes_back(self):
        filled = [ce.ChartRow(rank="1", title="Lilium", video_id="sm8673630"),
                  ce.ChartRow(rank="2", title="白花", video_id="sm8800000", time="2009-11-02")]
        with mock.patch.object(ce, "enrich_rows") as enrich:
            self.panel._enrich()
            for worker in self.panel._workers:
                worker.wait(5000)
            self.app.processEvents()
        enrich.assert_called_once()
        self.assertTrue(self.panel.enrich_button.isEnabled())
        self.assertIn("补全", self.panel.status_label.text())

    def test_opening_hint_reports_missing_main_ranks(self):
        """#53 的真事：主榜第 1～5 名没读到，打开「榜单」页就要提示补。"""
        work = _work()
        work.rows = [ce.ChartRow(rank=str(index), title=f"曲{index}")
                     for index in range(6, 21)]
        self.panel.start({"work": work})
        self.assertIn("缺第1、2、3、4、5名", self.panel.status_label.text())

    def test_reset_clears_everything(self):
        self.panel.reset()
        self.assertIsNone(self.panel.work)
        self.assertEqual(0, self.panel.table.rowCount())
        self.assertEqual("", self.panel.preview.toPlainText())
        self.assertEqual("", self.panel.fields["rule"].text())


class ChartFeatureTest(BasePanelTest):
    """侧栏「刊」：换功能只见榜单页 + 提交页，流程能拿到用户改完的 work。"""

    def setUp(self):
        import threading

        from utils.ui.window import MainWindow

        self.threading = threading
        self.window = MainWindow()

    def tearDown(self):
        self.window.deleteLater()
        super().tearDown()

    def _pump(self, predicate, timeout=10.0):
        import time

        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return predicate()

    def test_sidebar_order_and_icon(self):
        self.assertEqual(["entry", "producer", "vocalist", "chart"], self.window.sidebar.keys())
        button = self.window.sidebar._buttons[3]
        self.assertEqual("生成榜单条目", button.toolTip().splitlines()[0])
        self.assertEqual("chart", button.property("iconName"))

    def test_chart_feature_shows_only_its_own_pages(self):
        self.window.sidebar.feature_selected.emit("chart")
        self.assertEqual("chart", self.window.current_feature())
        for key in ("chart", "submit"):
            self.assertTrue(self.window.tabs.isTabVisible(self.window.page_index(key)), key)
        for key in ("style", "lyrics", "producer", "producer-style", "vocalist",
                    "vocalist-style"):
            self.assertFalse(self.window.tabs.isTabVisible(self.window.page_index(key)), key)

    def test_entries_feature_hides_the_chart_page(self):
        self.window.sidebar.feature_selected.emit("entry")
        self.assertFalse(self.window.tabs.isTabVisible(self.window.page_index("chart")))

    def test_chart_panel_has_a_scroll_area(self):
        index = self.window.page_index("chart")
        self.assertGreaterEqual(index, 0)
        self.assertIsInstance(self.window.tabs.widget(index), QtWidgets.QScrollArea)

    def test_run_chart_editor_round_trip(self):
        work = _work()
        answers = []
        thread = self.threading.Thread(target=lambda: answers.append(
            self.window.run_chart_editor(work)))
        thread.start()
        self.assertTrue(self._pump(lambda: self.window.tabs.currentIndex()
                              == self.window.page_index("chart")))
        panel = self.window.panels["chart"]
        self.assertIn("周刊UTAU排行榜52", panel.title_label.text())
        self.assertEqual(2, panel.table.rowCount())
        panel.saved.emit(work)
        thread.join(timeout=5)
        self.assertEqual([work], answers)


if __name__ == "__main__":
    import unittest

    unittest.main()
