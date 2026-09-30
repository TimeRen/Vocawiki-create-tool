"""歌姬模板那一套界面的单测（侧栏功能、曲目页、共用的样式页、提交页多页面）。

单独一个文件：`tests/utils/test_ui.py` 已经很大，而且那里的用例互相之间对
QApplication / 全局字号有依赖；这一组只碰歌姬模板相关的几页。
"""
import os
import tempfile
import threading
from pathlib import Path
from unittest import TestCase, mock

from PyQt5 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_WEBENGINE"] = "1"

from utils.ui.window import _Bridge as _UiBridge                              # noqa: E402
from utils.ui.window import install_exception_guard                           # noqa: E402

_guard_bridge = _UiBridge()
GUARDED_ERRORS = []
_guard_bridge.crashed.connect(lambda message, trace: GUARDED_ERRORS.append(message))
install_exception_guard(_guard_bridge)

from utils import vocalist_template as vt                                     # noqa: E402
from utils.ui.vocalist_panel import VocalistPanel                             # noqa: E402


def _pump(predicate, timeout=10.0):
    import time
    app = QtWidgets.QApplication.instance()
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return predicate()


def _work(split=True):
    return vt.VocalistWork(
        name="歌爱雪", engine="VOCALOID", split=split,
        songs=[
            vt.VocalistSong(title="强风大背头", ja="強風オールバック", year="2023",
                            date="2023-03-15", places=[("传说曲", "niconico"),
                                                       ("神话曲", "YouTube")],
                            source="殿堂页：VOCALOID传说曲/2023年投稿"),
            vt.VocalistSong(title="嘴唇核子弹", ja="くちびる核爆弾", year="2010",
                            date="2010-04-30", places=[("其他", "niconico")],
                            kind=vt.OTHER_UNHALL, source="分类"),
            vt.VocalistSong(title="深海(たると)", ja="深海", year="", places=[("其他", "niconico")],
                            kind=vt.OTHER_UNHALL, source="分类", flag="取不到投稿年"),
        ],
        styles={"titleBg": "#f38286", "titleFg": "#333333"},
        flags=[{"title": "深海(たると)", "ja": "深海", "reason": "取不到投稿年",
                "rank": "其他", "station": "niconico", "year": ""}],
        summary="210 首曲子 · 殿堂页 97 个 · 1 条待复核")


def _none_exist(titles):
    """「这些页维基上都没有」—— UI 用例里别去碰真网络（提交页会核一遍存在性）。"""
    return {str(title): False for title in titles}


class BasePanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面抛出过异常：{GUARDED_ERRORS}")


class VocalistPanelTest(BasePanelTest):
    """「曲目」页：表格、栏 / 站点 / 年份可改、恢复、人工复核、预览与保存。"""

    def setUp(self):
        self.panel = VocalistPanel()
        self.work = _work()
        self.panel.start({"work": self.work})

    def tearDown(self):
        self.panel.deleteLater()
        super().tearDown()

    def test_columns_and_rows(self):
        headers = [self.panel.table.horizontalHeaderItem(index).text()
                   for index in range(self.panel.table.columnCount())]
        self.assertEqual(["栏", "站点", "年份", "条目名", "日文名", "备注"], headers)
        self.assertEqual(3, self.panel.table.rowCount())
        self.assertEqual("神话曲", self.panel.table.item(0, 0).text())   # 最高那一栏
        self.assertEqual("YouTube", self.panel.table.item(0, 1).text())   # 最高那一栏的站点
        self.assertIn("神话曲/YouTube", self.panel.table.item(0, 5).text())
        self.assertIn("传说曲/niconico", self.panel.table.item(0, 5).text())
        self.assertIn("歌爱雪", self.panel.title_label.text())
        self.assertIn("VOCALOID", self.panel.title_label.text())
        self.assertIn("传说曲/niconico", self.panel.table.item(0, 5).text())
        self.assertIn("待复核", self.panel.count_label.text())

    def test_editing_rank_station_and_year(self):
        self.panel.table.item(0, 0).setText("殿堂曲")
        self.assertEqual([("殿堂曲", "niconico"), ("殿堂曲", "YouTube")],
                         self.work.songs[0].places)
        self.panel.table.item(0, 1).setText("bilibili、YouTube")
        self.assertEqual([("殿堂曲", "bilibili"), ("殿堂曲", "YouTube")],
                         self.work.songs[0].places)
        self.panel.table.item(0, 2).setText("2022")
        self.assertEqual("2022", self.work.songs[0].year)
        self.panel.table.item(0, 3).setText("强风大背头（改）")
        self.assertEqual("强风大背头（改）", self.work.songs[0].title)

    def test_other_kind_follows_the_rank_edit(self):
        self.panel.table.item(1, 0).setText("其他")
        self.assertEqual(vt.OTHER_UNHALL, self.work.songs[1].other_kind)

    def test_note_column_is_read_only(self):
        from PyQt5 import QtCore
        flags = self.panel.table.item(0, 5).flags()
        self.assertFalse(bool(flags & QtCore.Qt.ItemIsEditable))

    def test_remove_and_restore(self):
        self.panel.table.selectRow(0)
        self.panel._remove_songs()
        self.assertEqual(2, len(self.work.songs))
        self.panel._restore()
        self.assertEqual(3, len(self.work.songs))
        self.assertEqual("强风大背头", self.work.songs[0].title)

    def test_review_dialog_writes_back(self):
        with mock.patch.object(self.panel, "_review_song",
                               return_value=("accept", ("殿堂曲", ["bilibili"], "2024"))):
            self.panel._review_flags()
        song = self.work.songs[2]
        self.assertEqual("殿堂曲", song.rank)
        self.assertEqual([("殿堂曲", "bilibili")], song.places)
        self.assertEqual("2024", song.year)
        self.assertEqual("", song.flag)
        self.assertEqual([], self.work.flags)
        self.assertFalse(self.panel.review_button.isEnabled())
        self.assertIn("已处理 1 条", self.panel.status_label.text())

    def test_review_can_be_skipped(self):
        with mock.patch.object(self.panel, "_review_song", return_value=("stop", ("", [], ""))):
            self.panel._review_flags()
        self.assertEqual("取不到投稿年", self.work.songs[2].flag)

    def test_preview_and_page_list(self):
        text = self.panel.preview.toPlainText()
        self.assertIn("|name = 歌爱雪", text)
        self.assertIn("{{歌爱雪/2010|nocate=1", text)
        self.assertIn("Template:歌爱雪/2010", self.panel.pages_label.text())
        self.assertIn("Category:歌爱雪模板", self.panel.pages_label.text())   # 分类页也算一页
        self.assertIn("5 个页面", self.panel.count_label.text())

    def test_save_warns_about_yearless_songs_and_emits(self):
        seen = []
        self.panel.saved.connect(seen.append)
        self.panel._on_save()
        self.assertEqual([self.work], seen)
        self.assertIn("取不到投稿年", self.panel.status_label.text())

    def test_title_shows_the_subpages_only_mode(self):
        """「只新建年份子页、不动既有主模板」时标题上要写清楚（用户 2026-09-30）。"""
        work = _work()
        work.subpages_only = True
        self.panel.start({"work": work})
        self.assertIn("只新建年份子页", self.panel.title_label.text())
        # 这种模式只出年份子页（+ 它们挂的分类页）：列表里不该出现主模板（也不出文档页）
        self.assertEqual(["Category:歌爱雪模板", "Template:歌爱雪/2010",
                          "Template:歌爱雪/2023"],
                         self.panel.pages_label.text().split(" → "))
        self.assertIn("3 个页面", self.panel.count_label.text())

    def test_other_row_layout_switches_live(self):
        """用户 2026-09-30：曲目页上随时切「其他」栏平铺 / 按年份分层，预览立刻跟着变。"""
        work = _work(split=False)
        self.panel.start({"work": work})
        self.assertFalse(self.panel.other_years_box.isHidden())
        self.assertFalse(work.other_years)
        self.assertNotIn("2010年", self.panel.preview.toPlainText())   # 平铺：其他栏只有曲目
        self.panel.other_years_box.setChecked(True)
        self.assertTrue(work.other_years)
        text = self.panel.preview.toPlainText()
        self.assertIn("|group1 = 2010年", text)                     # 2010 只有那一首其他曲
        self.assertIn("|group2 = 年份未知", text)                    # 取不到年份的垫最后
        self.assertIn("已改成按年份分层", self.panel.status_label.text())
        self.panel.other_years_box.setChecked(False)
        self.assertFalse(work.other_years)
        self.assertNotIn("2010年", self.panel.preview.toPlainText())

    def test_other_row_switch_is_hidden_when_split(self):
        """拆成年份子页时子页里年份已经固定 → 这个开关没意义，藏起来。"""
        work = _work(split=True)
        self.panel.start({"work": work})
        self.assertTrue(self.panel.other_years_box.isHidden())

    def test_reset_clears_everything(self):
        self.panel.reset()
        self.assertEqual(0, self.panel.table.rowCount())
        self.assertEqual("", self.panel.preview.toPlainText())
        self.assertEqual("歌姬：—", self.panel.title_label.text())


class VocalistFeatureTest(BasePanelTest):
    """侧栏第三个功能：图标、标签页、曲目页 / 样式页的请求往返。"""

    def setUp(self):
        from utils.ui.window import MainWindow
        self.window = MainWindow()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def tearDown(self):
        self.window.deleteLater()
        super().tearDown()

    def test_sidebar_order_and_icon(self):
        self.assertEqual(["entry", "producer", "vocalist"], self.window.sidebar.keys())
        button = self.window.sidebar._buttons[2]
        self.assertEqual("生成歌姬模板", button.toolTip().splitlines()[0])
        self.assertEqual("singer", button.property("iconName"))

    def test_vocalist_feature_shows_only_its_own_pages(self):
        self.window.sidebar.feature_selected.emit("vocalist")
        self.assertEqual("vocalist", self.window.current_feature())
        for key in ("vocalist", "vocalist-style", "submit"):
            self.assertTrue(self.window.tabs.isTabVisible(self.window.page_index(key)), key)
        for key in ("style", "lyrics", "producer", "producer-style"):
            self.assertFalse(self.window.tabs.isTabVisible(self.window.page_index(key)), key)

    def test_pages_have_scroll_areas(self):
        for key in ("vocalist", "vocalist-style"):
            index = self.window.page_index(key)
            self.assertGreaterEqual(index, 0)
            self.assertIsInstance(self.window.tabs.widget(index), QtWidgets.QScrollArea)

    def test_works_panel_round_trip(self):
        work = _work()
        answers = []
        thread = threading.Thread(target=lambda: answers.append(
            self.window.run_vocalist_works(work)))
        thread.start()
        self.assertTrue(_pump(lambda: self.window.tabs.currentIndex()
                              == self.window.page_index("vocalist")))
        panel = self.window.panels["vocalist"]
        self.assertEqual(3, panel.table.rowCount())
        panel.saved.emit(work)
        thread.join(timeout=5)
        self.assertEqual([work], answers)

    def test_style_panel_uses_the_vocalist_payload(self):
        work = _work()
        answers = []
        image = Path(self.folder.name) / "yuki.png"
        image.write_bytes(b"png")
        payload = {"work": work, "build": vt.build_main_template,
                   "defaults": vt.effective_styles(work),
                   "picture": "https://voca.wiki/images/e/ee/Yuki_v4.jpg",
                   "loader": lambda: {"path": str(image)},
                   "picture_label": "歌姬立绘",
                   "picture_note": "",
                   "ai_note": "照立绘配色"}
        thread = threading.Thread(target=lambda: answers.append(
            self.window.run_vocalist_style(payload)))
        thread.start()
        self.assertTrue(_pump(lambda: self.window.tabs.currentIndex()
                              == self.window.page_index("vocalist-style")))
        panel = self.window.panels["vocalist-style"]
        self.assertTrue(_pump(lambda: panel.image_label.text() == "歌姬立绘"))
        self.assertEqual("#f38286", panel.fields["titleBg"].value())
        self.assertEqual("歌姬立绘", panel.avatar_button.text())
        self.assertIn("|name = 歌爱雪", panel.preview.toPlainText())
        self.assertIn("照立绘配色", panel._ai_note)
        panel._on_save()
        thread.join(timeout=5)
        self.assertEqual(1, len(answers))
        self.assertEqual("#f38286", answers[0]["titleBg"])
        self.assertEqual("", answers[0]["groupFg"])    # 歌姬模板的配色从空开始（用户 2026-09-30）


class SubmitMultiPageTest(BasePanelTest):
    """提交页的多页面：切换、保留改动、只提交当前页 / 全部提交。"""

    def setUp(self):
        from utils.vocalist_editor import VocalistTemplateApi
        from utils.ui.submit_panel import SubmitPanel
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        work = _work()
        specs = vt.page_specs(work)
        for index, spec in enumerate(specs):
            spec["file"] = Path(self.folder.name) / f"{index}_{spec['kind']}.wikitext"
        self.api = VocalistTemplateApi(work, specs)
        self.panel = SubmitPanel()

    def tearDown(self):
        self.panel.deleteLater()
        super().tearDown()

    def test_page_selector_lists_every_page(self):
        with mock.patch.object(self.api, "preview", return_value={"html": "x"}), \
                mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.pages_exist", side_effect=_none_exist), \
                mock.patch("utils.wiki_api.article_url", return_value="u"):
            self.panel.start({"api": self.api})
        # 分类页排最前（年份子页挂的就是它）
        self.assertFalse(self.panel.pages_row.isHidden())
        self.assertEqual(5, self.panel.pages_combo.count())
        self.assertIn("Category:歌爱雪模板", self.panel.pages_combo.itemText(0))
        self.assertIn("Template:歌爱雪/2010", self.panel.pages_combo.itemText(1))
        self.assertIn("共 5 个页面", self.panel.pages_hint.text())
        self.assertIn("Category:歌爱雪模板", self.panel.title_label.text())
        self.assertFalse(self.panel.skip_button.isHidden())

    def test_switching_pages_keeps_the_edited_text(self):
        with mock.patch.object(self.api, "preview", return_value={"html": "x"}), \
                mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.pages_exist", side_effect=_none_exist), \
                mock.patch("utils.wiki_api.article_url", return_value="u"):
            self.panel.start({"api": self.api})
            self.panel.editor.setPlainText("分类页改过的正文")
            self.panel.pages_combo.setCurrentIndex(3)          # 切到文档页
        self.assertEqual("分类页改过的正文", self.api.text_of(0))
        self.assertIn("本模板收录", self.panel.editor.toPlainText())
        with mock.patch.object(self.api, "preview", return_value={"html": "x"}):
            self.panel.pages_combo.setCurrentIndex(0)          # 切回来
        self.assertEqual("分类页改过的正文", self.panel.editor.toPlainText())

    def test_submit_only_touches_the_current_page(self):
        edits = []

        def fake_edit(page, text, summary):
            edits.append(page)
            return {"ok": True}

        with mock.patch.object(self.api, "preview", return_value={"html": "x"}), \
                mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.article_url", return_value="u"), \
                mock.patch("utils.wiki_api.pages_exist", side_effect=_none_exist), \
                mock.patch("utils.login.is_logged_in", return_value=True), \
                mock.patch("utils.wiki_api.edit_page", side_effect=fake_edit), \
                mock.patch.object(self.api, "plan_entries", return_value=[]):
            self.panel.start({"api": self.api})
            self.panel.submit_button.click()
            self.assertTrue(_pump(lambda: not self.panel._busy))
        self.assertEqual(["Category:歌爱雪模板"], edits)
        # 多页面（歌姬模板）：交完这一页还不能收摊（下面还有 4 页）
        self.assertFalse(self.panel._finished)
        self.assertTrue(self.panel.submit_all_button.isEnabled())

    def test_submit_all_writes_every_page(self):
        edits = []

        def fake_edit(page, text, summary):
            edits.append(page)
            return {"ok": True}

        backlinks = [{"title": "歌爱雪", "count": 1, "kind": "加入本模板"}]
        with mock.patch.object(self.api, "preview", return_value={"html": "x"}), \
                mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.article_url", return_value="u"), \
                mock.patch("utils.wiki_api.pages_exist", side_effect=_none_exist), \
                mock.patch("utils.login.is_logged_in", return_value=True), \
                mock.patch("utils.wiki_api.edit_page", side_effect=fake_edit), \
                mock.patch.object(self.api, "plan_entries", return_value=backlinks), \
                mock.patch.object(self.panel, "_show_backlink_dialog") as dialog:
            self.panel.start({"api": self.api})
            self.panel.submit_all_button.click()
            self.assertTrue(_pump(lambda: not self.panel._busy))
        self.assertEqual(["Category:歌爱雪模板", "Template:歌爱雪/2010",
                          "Template:歌爱雪/2023", "Template:歌爱雪/doc",
                          "Template:歌爱雪"], edits)
        dialog.assert_called_once()
        self.assertIn("5 个页面", self.panel.status_label.toolTip())

    # —— 跳过（用户 2026-09-30）——
    def _start(self, exists=None):
        """开这一页（顺手把「页面在不在」的问询挡掉，别去碰真网络）。"""
        with mock.patch.object(self.api, "preview", return_value={"html": "x"}), \
                mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.pages_exist", side_effect=exists or _none_exist), \
                mock.patch("utils.wiki_api.article_url", return_value="u"):
            self.panel.start({"api": self.api})

    def test_skip_button_marks_the_page_and_moves_on(self):
        """点「跳过」= 这一页不交，接着看下一页；翻回来还能取消。"""
        with mock.patch("utils.wiki_api.pages_exist", side_effect=_none_exist):
            self._start()
            self.assertEqual("跳过", self.panel.skip_button.text())
            self.panel.skip_button.click()
            self.assertEqual(1, self.panel.pages_combo.currentIndex())
            self.assertTrue(self.api.pages()[0]["skipped"])
            self.assertIn("已跳过", self.panel.pages_combo.itemText(0))
            self.assertIn("已跳过 1 页", self.panel.pages_hint.text())
            self.assertEqual("跳过", self.panel.skip_button.text())   # 新的一页没跳过
            self.panel.pages_combo.setCurrentIndex(0)
            self.assertEqual("取消跳过", self.panel.skip_button.text())
            self.panel.skip_button.click()
            self.assertFalse(self.api.pages()[0]["skipped"])
            self.assertNotIn("已跳过", self.panel.pages_combo.itemText(0))

    def test_skip_existing_marks_the_pages_the_wiki_has(self):
        """「跳过已存在的」：维基上已经有的页一次标上（列表里也写「维基上已有」）。"""
        found = {"Category:歌爱雪模板": True, "Template:歌爱雪/2010": False,
                 "Template:歌爱雪/2023": True, "Template:歌爱雪/doc": False,
                 "Template:歌爱雪": True}

        def fake_exists(titles):
            return {str(title): found.get(str(title), False) for title in titles}

        with mock.patch("utils.wiki_api.pages_exist", side_effect=fake_exists):
            self._start(exists=fake_exists)
            self.assertTrue(_pump(lambda: "维基上已有" in self.panel.pages_combo.itemText(0)))
            self.assertIn("维基上已有 3 页", self.panel.pages_hint.text())
            self.panel.skip_existing_button.click()
            self.assertTrue(_pump(lambda: "已跳过 3 个" in self.panel.status_label.text()))
            self.assertIn("已跳过", self.panel.pages_combo.itemText(2))
            # 跳到第一个还能交的页（2010 年那页）
            self.assertEqual(1, self.panel.pages_combo.currentIndex())

    def test_single_page_api_hides_the_selector(self):
        api = mock.Mock()
        api.get_context.return_value = {
            "kind": "template", "page": "Template:雄之助", "file": "x.wikitext",
            "origin": "o", "summary": "摘要", "canSubmit": True, "createRedirect": False,
            "cover": None, "family": {"available": False}, "disambig": {"needed": False},
        }
        api._wikitext = "正文"
        api.preview.return_value = {"html": "x"}
        with mock.patch("utils.wiki_api.origin", return_value="o"), \
                mock.patch("utils.wiki_api.article_url", return_value="u"):
            self.panel.start({"api": api})
        self.assertTrue(self.panel.pages_row.isHidden())


if __name__ == "__main__":
    import unittest
    unittest.main()
