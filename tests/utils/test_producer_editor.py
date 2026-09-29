"""`utils/producer_editor.py` 的单测：提交页的数据侧与那条流程（Qt 相关的部分 mock 掉）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import producer_editor as pe
from utils import producer_template as pt


def _artist() -> pt.ProducerArtist:
    return pt.ProducerArtist(id=23981, name="雄之助", artist_type="Producer")


def _work(songs=None, albums=("Void",), styles=None) -> pt.ProducerWork:
    return pt.ProducerWork(
        artist=_artist(),
        songs=songs if songs is not None else [
            pt.ProducerSong(ja="ラグタイムレコード", cn="时滞记录", date="2021-09-01"),
            pt.ProducerSong(ja="Navy", date="2024-01-05")],
        albums=list(albums), page_name="雄之助", template_name="雄之助",
        styles=dict(styles or pt.DEFAULT_STYLES))


class NameHelperTest(unittest.TestCase):
    def test_template_title(self):
        self.assertEqual("Template:雄之助", pe.template_title("雄之助"))
        self.assertEqual("Template:雄之助", pe.template_title("Template:雄之助"))
        self.assertEqual("Template:", pe.template_title(""))

    def test_bare_template_name(self):
        self.assertEqual("雄之助", pe.bare_template_name("Template:雄之助"))
        self.assertEqual("雄之助", pe.bare_template_name("雄之助"))

    def test_output_path(self):
        with mock.patch.object(pe, "get_output_path", return_value=Path("/tmp/out")):
            self.assertEqual(Path("/tmp/out/P主模板_雄之助.wikitext"),
                             pe.output_path("Template:雄之助"))


class ApiContextTest(unittest.TestCase):
    def setUp(self):
        self.work = _work()
        self.api = pe.ProducerTemplateApi(self.work, Path("/tmp/x.wikitext"), "正文")

    def test_get_context_marks_it_as_a_template(self):
        with mock.patch.object(pe.login, "is_logged_in", return_value=True), \
                mock.patch.object(pe.wiki_api, "article_url", return_value="https://x/T"), \
                mock.patch.object(pe.wiki_api, "origin", return_value="https://voca.wiki/"):
            context = self.api.get_context()
        self.assertEqual("template", context["kind"])
        self.assertEqual("Template:雄之助", context["page"])
        self.assertTrue(context["canSubmit"])
        self.assertFalse(context["family"]["available"])
        self.assertFalse(context["disambig"]["needed"])
        self.assertIsNone(context["cover"])

    def test_preview_renders_as_the_template_page(self):
        with mock.patch.object(pe.wiki_api, "parse_wikitext",
                               return_value={"html": "<p>x</p>"}) as parse:
            self.assertEqual({"html": "<p>x</p>"}, self.api.preview("{{Navbox}}"))
        parse.assert_called_once_with("{{Navbox}}", title="Template:雄之助")

    def test_save_writes_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).joinpath("sub", "t.wikitext")
            api = pe.ProducerTemplateApi(_work(), path, "")
            self.assertEqual({"ok": True, "message": "已保存到本地文件"}, api.save("模板正文"))
            self.assertEqual("模板正文", path.read_text(encoding="utf-8"))


class PlanEntriesTest(unittest.TestCase):
    def setUp(self):
        self.work = _work()
        self.text = pt.build_template(self.work)
        self.api = pe.ProducerTemplateApi(self.work, Path("/tmp/x.wikitext"), self.text)

    def test_plan_entries_marks_existing_missing_and_already_tagged(self):
        with mock.patch.object(pe.wiki_api, "fetch_pages_text",
                               return_value={"雄之助": "正文\n{{雄之助}}\n== 注释 ==\n",
                                             "时滞记录": "正文\n== 注释 ==\n"}):
            entries = self.api.plan_entries(self.text)
        rows = {item["title"]: item for item in entries}
        self.assertEqual("雄之助", entries[0]["title"], "P主条目排在最前面")
        self.assertEqual(0, rows["雄之助"]["count"])            # 已经有本模板了
        self.assertEqual("已有本模板", rows["雄之助"]["note"])
        self.assertEqual(1, rows["时滞记录"]["count"])
        self.assertEqual("加入本模板", rows["时滞记录"]["note"])
        self.assertEqual(0, rows["Navy"]["count"])              # 条目还没建
        self.assertEqual("条目还没建", rows["Navy"]["note"])


class SubmitTest(unittest.TestCase):
    def setUp(self):
        self.work = _work()
        self.api = pe.ProducerTemplateApi(self.work, Path("/tmp/x.wikitext"), "")

    def test_submit_needs_login(self):
        with mock.patch.object(pe.login, "is_logged_in", return_value=False):
            self.assertFalse(self.api.submit("正文")["ok"])

    def test_submit_reports_edit_failure(self):
        with mock.patch.object(pe.login, "is_logged_in", return_value=True), \
                mock.patch.object(pe.wiki_api, "edit_page",
                                  return_value={"ok": False, "error": "被挡了"}), \
                mock.patch.object(self.api, "_write_local", return_value=True):
            result = self.api.submit("正文")
        self.assertFalse(result["ok"])
        self.assertIn("被挡了", result["error"])

    def test_submit_returns_backlinks_and_dialog_texts(self):
        text = pt.build_template(self.work)
        with mock.patch.object(pe.login, "is_logged_in", return_value=True), \
                mock.patch.object(pe.wiki_api, "edit_page",
                                  return_value={"ok": True, "newrevid": 7}), \
                mock.patch.object(pe.wiki_api, "article_url", return_value="https://x/T"), \
                mock.patch.object(pe.wiki_api, "fetch_pages_text",
                                  return_value={"雄之助": "正文", "时滞记录": "正文"}), \
                mock.patch.object(self.api, "_write_local", return_value=True):
            result = self.api.submit(text, summary="摘要")
        self.assertTrue(result["ok"])
        self.assertEqual("https://x/T", result["url"])
        self.assertEqual("把模板加进条目", result["backlinkTitle"])
        self.assertIn("{{雄之助}}", result["backlinkHeader"])
        self.assertEqual("写入选中条目", result["backlinkAction"])
        self.assertIn("条目还没建", result["backlinkSkipNote"])
        self.assertEqual(["雄之助", "时滞记录", "Navy"],
                         [item["title"] for item in result["backlinks"]])

    def test_fix_backlinks_writes_through_insert_into_pages(self):
        seen = []

        def fake_insert(name, titles, progress=None, summary=""):
            seen.append((name, titles))
            return [{"title": title, "ok": True, "count": 1} for title in titles]

        with mock.patch.object(pe.producer_template, "insert_into_pages",
                               side_effect=fake_insert):
            result = self.api.fix_backlinks(json.dumps(["时滞记录", "Navy"]))
        self.assertTrue(result["ok"])
        self.assertEqual([("雄之助", ["时滞记录", "Navy"])], seen)
        self.assertIn("已把模板写进 2 个条目", result["message"])

    def test_fix_backlinks_rejects_bad_input(self):
        self.assertFalse(self.api.fix_backlinks("不是 JSON")["ok"])
        self.assertFalse(self.api.fix_backlinks("[]")["ok"])


class WorkSetupTest(unittest.TestCase):
    def test_prepare_work_fetches_songs_and_albums(self):
        with mock.patch.object(pe.producer_template, "fetch_songs",
                               return_value=[pt.ProducerSong(ja="A")]) as songs, \
                mock.patch.object(pe.producer_template, "fetch_albums",
                                  return_value=["Void"]) as albums:
            work = pe.prepare_work(_artist())
        songs.assert_called_once_with(23981)
        albums.assert_called_once_with(23981)
        self.assertEqual(["A"], [song.ja for song in work.songs])
        self.assertEqual(["Void"], work.albums)
        self.assertEqual("雄之助", work.page_name)
        self.assertEqual("雄之助", work.template_name)

    def test_choose_artist_asks_again_when_not_found(self):
        with mock.patch.object(pe, "prompt_response", side_effect=["没这个人", "雄之助"]) as ask, \
                mock.patch.object(pe.producer_template, "search_artists",
                                  side_effect=[[], [_artist()]]):
            self.assertEqual("雄之助", pe.choose_artist("").name)
        self.assertEqual(2, ask.call_count)

    def test_choose_artist_gives_up_on_empty_answer(self):
        with mock.patch.object(pe, "prompt_response", side_effect=["没这个人", ""]), \
                mock.patch.object(pe.producer_template, "search_artists", return_value=[]):
            self.assertIsNone(pe.choose_artist(""))

    def test_choose_artist_asks_which_one_when_several(self):
        other = pt.ProducerArtist(id=2, name="雄之助（真人）", artist_type="Producer")
        with mock.patch.object(pe.producer_template, "search_artists",
                               return_value=[_artist(), other]), \
                mock.patch.object(pe, "prompt_choices", return_value=2) as choose:
            self.assertEqual(2, pe.choose_artist("雄之助").id)
        self.assertIn("雄之助（Producer）", choose.call_args.args[1][0])


class FlowTest(unittest.TestCase):
    """终端模式那条路：不问界面，只把模板文件写出来。"""

    def test_generate_in_console_mode_writes_the_file(self):
        work = _work()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with mock.patch.object(pe, "get_output_path", return_value=output), \
                    mock.patch.object(pe, "choose_artist", return_value=_artist()), \
                    mock.patch.object(pe, "prepare_work", return_value=work), \
                    mock.patch.object(pe.producer_template, "resolve_from_wiki") as resolve, \
                    mock.patch("utils.ui.is_active", return_value=False), \
                    mock.patch("utils.ui.open_folder") as opened:
                path = pe.generate_producer_template()
            self.assertEqual(output.joinpath("P主模板_雄之助.wikitext"), path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("|name=雄之助", text)
            self.assertIn("时滞记录{{!}}{{lj|ラグタイムレコード}}", text)
            opened.assert_called_once_with(path)
        resolve.assert_called_once()

    def test_generate_returns_none_when_cancelled(self):
        with mock.patch.object(pe, "choose_artist", return_value=None), \
                mock.patch("utils.ui.is_active", return_value=False):
            self.assertIsNone(pe.generate_producer_template())

    def test_generate_stops_when_the_works_panel_is_cancelled(self):
        work = _work()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(pe, "get_output_path", return_value=Path(folder)), \
                mock.patch.object(pe, "choose_artist", return_value=_artist()), \
                mock.patch.object(pe, "prepare_work", return_value=work), \
                mock.patch.object(pe.producer_template, "resolve_from_wiki"), \
                mock.patch("utils.ui.is_active", return_value=True), \
                mock.patch.object(pe, "open_works_editor", return_value=None):
            self.assertIsNone(pe.generate_producer_template())
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_generate_applies_styles_from_the_style_panel(self):
        work = _work()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with mock.patch.object(pe, "get_output_path", return_value=output), \
                    mock.patch.object(pe, "choose_artist", return_value=_artist()), \
                    mock.patch.object(pe, "prepare_work", return_value=work), \
                    mock.patch.object(pe.producer_template, "resolve_from_wiki"), \
                    mock.patch("utils.ui.is_active", return_value=True), \
                    mock.patch.object(pe, "open_works_editor", return_value=work), \
                    mock.patch.object(pe, "open_style_editor",
                                      return_value={"titleBg": "#123456", "titleFg": ""}), \
                    mock.patch("utils.ui.open_template_submit", return_value=True) as submit:
                path = pe.generate_producer_template()
            text = path.read_text(encoding="utf-8")
            self.assertIn("|titlestyle = background:#123456", text)
            self.assertEqual("#123456", work.styles["titleBg"])
        self.assertEqual(1, submit.call_count)


if __name__ == "__main__":
    unittest.main()
