"""榜单页那一套界面的单测（侧栏「刊」、榜单卡片、生成按钮）。

单独一个文件（与 `test_vocalist_ui.py` 同理）：`test_ui.py` 已经很大，这一组只碰榜单页。
用户 2026-11-17 要求把横向表格改成**每首一块卡片**，所以这里按下标取 `panel.cards`。
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
from utils import chart_entry, chart_nico                                       # noqa: E402
from utils.ui.chart_panel import ChartPanel                                     # noqa: E402


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


def _type(card, name, text):
    """像用户那样改一格（回车 / 失焦就是 `editingFinished`）。"""
    edit = card.rank_edit if name == "rank" else card.field(name)
    edit.setText(text)
    edit.editingFinished.emit()


class BasePanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面抛出过异常：{GUARDED_ERRORS}")


class ChartPanelTest(BasePanelTest):
    """「榜单」页：卡片、每一格可改、加卡删卡恢复、预览与生成。"""

    def setUp(self):
        self.panel = ChartPanel()
        self.work = _work()
        self.panel.start({"work": self.work})

    def tearDown(self):
        self.panel.deleteLater()
        super().tearDown()

    def test_cards_show_one_row_each(self):
        self.assertEqual(2, len(self.panel.cards))
        first = self.panel.cards[0]
        self.assertEqual("1", first.rank_edit.text())
        self.assertEqual("Lilium", first.field("title").text())
        self.assertEqual("重音Teto", first.field("vocalist").text())
        self.assertEqual("评论权重 = 0.97", first.field("other").text())
        self.assertIn("周刊UTAU排行榜52", self.panel.title_label.text())
        self.assertIn("2个名次", self.panel.summary_label.text())

    def test_big_areas_live_in_three_sub_tabs(self):
        """用户 2026-11-19：三块硬堆在一条竖线上，1036×752 的窗口里互相重叠。"""
        labels = [self.panel.tabs.tabText(index) for index in range(self.panel.tabs.count())]
        self.assertEqual(["榜单卡片", "视频简介", "wikitext预览"], labels)
        self.assertIs(self.panel.cards_page, self.panel.scroll.parentWidget())
        self.assertIs(self.panel.text_page, self.panel.description.parentWidget())
        self.assertIs(self.panel.text_page, self.panel.notes.parentWidget())
        self.assertIs(self.panel.preview_page, self.panel.preview.parentWidget())

    def test_page_fits_a_752px_window(self):
        """整页最小高度必须小于 1036×752 里能给出的高度（685px），否则 Qt 会压到重叠。"""
        self.panel.resize(948, 685)
        self.app.processEvents()
        self.assertLessEqual(self.panel.minimumSizeHint().height(), 660)
        self.assertLessEqual(self.panel.minimumSizeHint().width(), 948)

    def test_status_stays_on_one_line(self):
        """用户 2026-11-19 的截图里，页脚那段长提示换成了四行还把界面撑开。"""
        long_text = ("番外榜缺第1、2、3、4、5、6、7、8、9、10名"
                     "（选中上一条点「添加行」补）；第1、3、4名只有曲名没有数字")
        self.panel.set_status(long_text)
        self.assertLessEqual(len(self.panel.status_label.text()), 40)
        self.assertTrue(self.panel.status_label.text().endswith("…"))
        self.assertEqual(long_text, self.panel.status_label.toolTip())

    def test_adding_a_row_switches_to_the_cards_tab(self):
        self.panel.tabs.setCurrentWidget(self.panel.preview_page)
        self.panel._add_row()
        self.assertIs(self.panel.cards_page, self.panel.tabs.currentWidget())

    def test_section_and_badge_are_dropdowns(self):
        card = self.panel.cards[0]
        section = card.section_box
        badge = card.badge_box
        self.assertEqual(["主榜", "番外"], [section.itemText(index)
                                          for index in range(section.count())])
        self.assertEqual(["（无）", "PICKUP", "TOPIC", "上周冠军", "HISTORY", "OP", "ED"],
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

    def test_editing_a_card_field_reaches_the_preview(self):
        _type(self.panel.cards[1], "score", "9999")
        self.assertEqual("9999", self.work.rows[1].score)
        self.assertIn("|得点 = 9999", self.panel.preview.toPlainText())

    def test_editing_the_rank_reaches_the_model(self):
        _type(self.panel.cards[1], "rank", "12")
        self.assertEqual("12", self.work.rows[1].rank)
        self.assertIn("|本周 = 12", self.panel.preview.toPlainText())

    def test_editing_other_params_reaches_the_model(self):
        _type(self.panel.cards[1], "other", "翻唱 = 1")
        self.assertEqual({"翻唱": "1"}, self.work.rows[1].other)
        self.assertIn("|翻唱 = 1", self.panel.preview.toPlainText())

    def test_picking_a_badge_adds_color_and_bottom_column(self):
        card = self.panel.cards[0]
        card.badge_box.setCurrentText("TOPIC")
        self.assertEqual("TOPIC", self.work.rows[0].badge)
        self.assertEqual("TOPIC", card.badge_label.text())
        text = self.panel.preview.toPlainText()
        self.assertIn("|color = #01BB00", text)
        self.assertIn("|bottom-column = {{color|#01BB00|T O P I C}}", text)

    def test_section_dropdown_moves_the_card_to_the_extra_list(self):
        self.panel.cards[1].section_box.setCurrentText("番外")
        self.assertEqual("番外", self.work.rows[1].section)
        self.assertIn("=== UTAU番外榜 ===", self.panel.preview.toPlainText())

    def test_editing_the_cover_field_reaches_the_preview(self):
        _type(self.panel.cards[0], "cover", "1")
        self.assertEqual("1", self.work.rows[0].cover)
        self.assertIn("|翻唱 = 1", self.panel.preview.toPlainText())

    def test_note_shows_under_the_card(self):
        self.assertFalse(self.panel.cards[0].note_label.text())
        self.work.rows[0].note = "没有稿件编号"
        self.panel.start({"work": self.work})
        self.assertEqual("没有稿件编号", self.panel.cards[0].note_label.text())

    def test_typed_title_pulls_the_remembered_translation(self):
        """手填的曲名命中 user_names.yaml → 自动补「条目」。"""
        from utils import user_dict

        user_dict._cache = {"vocalists": {}, "titles": {"白花": "白花(usimitsudoki)"}}
        try:
            _type(self.panel.cards[0], "title", "白花")
            self.assertEqual("白花(usimitsudoki)", self.work.rows[0].entry)
            self.assertEqual("白花(usimitsudoki)", self.panel.cards[0].field("entry").text())
        finally:
            user_dict._cache = None

    def test_header_edit_changes_the_rule_and_navbox(self):
        self.panel.fields["rule"].setText("2023")
        self.panel._on_header_edited()
        self.assertIn("{{周刊VOCAL Character & UTAU RANKING|2023}}",
                      self.panel.preview.toPlainText())

    def test_add_remove_and_restore(self):
        self.panel._add_row()
        self.assertEqual(3, len(self.panel.cards))
        self.assertEqual(3, len(self.work.rows))
        self.panel._on_card_picked(self.panel.cards[2], False)
        self.panel._remove_rows()
        self.assertEqual(2, len(self.panel.cards))
        self.panel._restore()
        self.assertEqual(2, len(self.work.rows))
        self.assertEqual("Lilium", self.work.rows[0].title)

    def test_adding_after_a_selected_card_keeps_the_position(self):
        """视频漏了一个名次时，新卡片要插在选中那张下面（不是永远追加到末尾）。"""
        self.panel._on_card_picked(self.panel.cards[0], False)
        self.panel._add_row()
        self.assertEqual(3, len(self.work.rows))
        self.assertEqual("", self.work.rows[1].rank)
        self.assertEqual("白花", self.work.rows[2].title)

    def test_ctrl_click_removes_several_cards(self):
        self.panel._on_card_picked(self.panel.cards[0], False)
        self.panel._on_card_picked(self.panel.cards[1], True)
        self.assertEqual([0, 1], self.panel._selected)
        self.panel._remove_rows()
        self.assertEqual([], self.work.rows)

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
        with mock.patch.object(ce, "enrich_rows", return_value=filled) as enrich, \
             mock.patch.object(chart_entry, "fill_entries", return_value=0):
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
        self.assertEqual([], self.panel.cards)
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

    def test_chart_page_scrolls_its_cards(self):
        """榜单页现在外面也套了一层滚动区（窗口过窄/过矮时兜底），卡片在子标签页里。"""
        index = self.window.page_index("chart")
        self.assertGreaterEqual(index, 0)
        area = self.window.tabs.widget(index)
        self.assertIsInstance(area, QtWidgets.QScrollArea)
        panel = area.widget()
        self.assertIsInstance(panel, ChartPanel)
        self.assertIsInstance(panel.scroll, QtWidgets.QScrollArea)
        self.assertIs(panel, self.window.panels["chart"])

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
        self.assertEqual(2, len(panel.cards))
        panel.saved.emit(work)
        thread.join(timeout=5)
        self.assertEqual([work], answers)


if __name__ == "__main__":
    import unittest

    unittest.main()
