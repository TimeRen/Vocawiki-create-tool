"""`utils/vocalist_editor.py` 的单测：提交页的数据侧（多页面）与那条流程（界面 mock 掉）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import vocalist_editor as ve
from utils import vocalist_template as vt
from utils import ui as ui_facade


def _work(split=True, name="重音Teto"):
    return vt.VocalistWork(
        name=name, engine="UTAU", split=split,
        songs=[vt.VocalistSong(title="2代目閻魔", ja="2代目閻魔", year="2024",
                               date="2024-12-14", places=[("殿堂曲", "niconico")]),
               vt.VocalistSong(title="テトリス", ja="テトリス", year="2024",
                               date="2024-11-18", places=[("破亿播放曲目", "YouTube")]),
               vt.VocalistSong(title="旧曲", ja="旧曲", year="2008",
                               date="2008-04-01", places=[("其他", "niconico")],
                               kind=vt.OTHER_UNHALL)],
        styles={"titleBg": "#d93a49", "titleFg": "#f2dfe6"})


def _specs(folder: Path):
    work = _work()
    specs = vt.page_specs(work)
    for index, spec in enumerate(specs):
        spec["file"] = folder / f"{index}_{spec['kind']}.wikitext"
    return work, specs


def _none_exist(titles):
    """「这些页维基上都没有」—— 单测里别去碰真网络（分页 / 分类页都要问一句）。"""
    return {str(title): False for title in titles}


class NameHelperTest(unittest.TestCase):
    def test_template_title(self):
        self.assertEqual("Template:歌爱雪", ve.template_title("歌爱雪"))
        self.assertEqual("Template:歌爱雪", ve.template_title("Template:歌爱雪"))
        self.assertEqual("Template:", ve.template_title(""))

    def test_bare_template_name(self):
        self.assertEqual("歌爱雪", ve.bare_template_name("Template:歌爱雪"))
        self.assertEqual("歌爱雪", ve.bare_template_name("歌爱雪"))


class AskTest(unittest.TestCase):
    """三种生成方式（用户 2026-09-30）：拆成年份子页 + 重写主模板 / 只新建年份子页 /
    不拆一个页面写完整。`Template:<歌姬>` 已经存在时才给第二个选项。"""

    def test_existing_template_defaults_to_split(self):
        with mock.patch.object(ve, "prompt_choices", return_value=1) as choices:
            self.assertEqual(ve.LAYOUT_SPLIT, ve.ask_layout("重音Teto", exists=True))
        self.assertIn("已经有 Template:重音Teto 了", choices.call_args[0][0])

    def test_existing_template_can_keep_the_main_template_untouched(self):
        """「只新建年份子页、不动既有主模板」：初音未来 那种手写大导航框用这个。"""
        with mock.patch.object(ve, "prompt_choices", return_value=2) as choices:
            self.assertEqual(ve.LAYOUT_SUBPAGES_ONLY, ve.ask_layout("初音未来", exists=True))
        self.assertIn("不动", choices.call_args[0][0])
        self.assertTrue(any("只新建年份子页" in choice
                            for choice in choices.call_args[0][1]))

    def test_choosing_the_last_option_keeps_one_page(self):
        with mock.patch.object(ve, "prompt_choices", return_value=3):
            self.assertEqual(ve.LAYOUT_ONE_PAGE, ve.ask_layout("歌爱雪", exists=True))

    def test_new_template_prompt_mentions_that_it_is_new_and_needs_one_page(self):
        with mock.patch.object(ve, "prompt_choices", return_value=1) as choices:
            self.assertEqual(ve.LAYOUT_SPLIT, ve.ask_layout("某歌姬", exists=False))
        self.assertIn("还没有", choices.call_args[0][0])
        self.assertEqual(3, len(choices.call_args[0][1]))

    def test_without_an_existing_template_the_second_option_means_split(self):
        """还没有那个模板时选第 2 项（只新建年份子页）没有意义 → 按拆分走。"""
        with mock.patch.object(ve, "prompt_choices", return_value=2):
            self.assertEqual(ve.LAYOUT_SPLIT, ve.ask_layout("某歌姬", exists=False))

    def test_other_row_defaults_to_flat(self):
        """「其他」栏的曲目平铺还是按年份分层：默认（第 1 项）= 平铺（站上主流）。"""
        with mock.patch.object(ve, "prompt_choices", return_value=1) as choices:
            self.assertFalse(ve.ask_other_layout("弗里摩侠"))
        self.assertIn("其他", choices.call_args[0][0])
        self.assertEqual(2, len(choices.call_args[0][1]))

    def test_other_row_can_be_grouped_by_year(self):
        with mock.patch.object(ve, "prompt_choices", return_value=2):
            self.assertTrue(ve.ask_other_layout("弗里摩侠"))


class MultiPageApiTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.work, self.specs = _specs(Path(self.folder.name))
        self.api = ve.VocalistTemplateApi(self.work, self.specs)

    def tearDown(self):
        self.folder.cleanup()

    def test_pages_in_submit_order(self):
        """分类页排在最前（年份子页挂的就是它），然后年份子页 → 文档 → 主模板。"""
        self.assertEqual(["Category:重音Teto模板", "Template:重音Teto/2008",
                          "Template:重音Teto/2024", "Template:重音Teto/doc",
                          "Template:重音Teto"],
                         [page["name"] for page in self.api.pages()])

    def test_context_carries_the_page_list(self):
        with mock.patch.object(ve.wiki_api, "article_url", return_value="https://x/T"), \
                mock.patch.object(ve.wiki_api, "origin", return_value="https://voca.wiki/"):
            context = self.api.get_context()
        self.assertEqual("template", context["kind"])
        self.assertEqual("Category:重音Teto模板", context["page"])
        self.assertEqual(0, context["pageIndex"])
        self.assertEqual(5, len(context["pages"]))
        self.assertFalse(context["skipped"])
        self.assertIsNone(context["exists"])              # 还没查过
        self.assertFalse(context["family"]["available"])
        self.assertFalse(context["disambig"]["needed"])

    def test_select_switches_the_page_and_its_text(self):
        text = self.api.select(2)
        self.assertIn("2024年歌曲", text or "")
        with mock.patch.object(ve.wiki_api, "article_url", return_value="u"), \
                mock.patch.object(ve.wiki_api, "origin", return_value="o"):
            self.assertEqual("Template:重音Teto/2024", self.api.get_context()["page"])

    def test_set_text_is_kept_per_page(self):
        self.api.select(0)
        self.api.set_text("改过的正文")
        self.api.select(1)
        self.api.select(0)
        self.assertEqual("改过的正文", self.api._wikitext)

    def test_out_of_range_select_returns_none(self):
        self.assertIsNone(self.api.select(99))

    def test_preview_uses_the_current_page_title(self):
        self.api.select(3)
        with mock.patch.object(ve.wiki_api, "parse_wikitext",
                               return_value={"html": "x"}) as parse:
            self.api.preview("正文")
        parse.assert_called_once_with("正文", title="Template:重音Teto/doc")

    def test_save_writes_the_current_page(self):
        self.api.select(1)
        self.assertEqual({"ok": True, "message": "已保存到本地文件：1_year.wikitext"},
                         self.api.save("新正文"))
        self.assertEqual("新正文", Path(self.specs[1]["file"]).read_text(encoding="utf-8"))

    def test_submit_writes_and_edits_only_the_current_page(self):
        self.api.select(4)
        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page",
                                  return_value={"ok": True, "newrevid": 1}) as edit, \
                mock.patch.object(ve.wiki_api, "pages_exist", side_effect=_none_exist), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            result = self.api.submit("主模板正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertEqual("Template:重音Teto", edit.call_args[0][0])
        self.assertEqual("主模板正文", edit.call_args[0][1])
        self.assertEqual("u", result["url"])
        self.assertTrue(result["backlinks"] == [])

    def test_submit_all_skips_pages_that_did_not_change(self):
        edits = []

        def fake_edit(page, text, summary):
            edits.append(page)
            return {"ok": True}

        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page", side_effect=fake_edit), \
                mock.patch.object(ve.wiki_api, "pages_exist", side_effect=_none_exist), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            first = self.api.submit_all("摘要")
            second = self.api.submit_all("摘要")
        self.assertTrue(first["ok"])
        self.assertEqual(5, len(edits))
        self.assertEqual("已提交 5 个页面", first["message"])
        self.assertEqual(5, len(edits))                  # 第二趟全跳过，不再提交
        self.assertIn("跳过", second["message"])

    def test_submit_all_reports_failures_but_keeps_going(self):
        calls = {"n": 0}

        def fake_edit(page, text, summary):
            calls["n"] += 1
            return {"ok": calls["n"] != 2, "error": "站点出错"}

        messages = []
        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page", side_effect=fake_edit), \
                mock.patch.object(ve.wiki_api, "pages_exist", side_effect=_none_exist), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            result = self.api.submit_all("摘要", progress=messages.append)
        self.assertFalse(result["ok"])
        self.assertEqual(5, calls["n"])
        self.assertIn("已提交 4 个页面", result["message"])
        self.assertTrue(any("提交失败" in message for message in messages))

    # —— 跳过（用户 2026-09-30）——
    def test_skip_takes_a_page_out_of_submit_all(self):
        """标了跳过的页「全部提交」不碰（年份子页之前传过时用）。"""
        edits = []

        def fake_edit(page, text, summary):
            edits.append(page)
            return {"ok": True}

        self.assertTrue(self.api.skip(1)["ok"])
        page = self.api.pages()[1]
        self.assertEqual("Template:重音Teto/2008", page["name"])
        self.assertTrue(page["skipped"])
        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page", side_effect=fake_edit), \
                mock.patch.object(ve.wiki_api, "pages_exist", side_effect=_none_exist), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            result = self.api.submit_all("摘要")
        self.assertNotIn("Template:重音Teto/2008", edits)
        self.assertEqual(4, len(edits))
        self.assertEqual("已提交 4 个页面（1 个跳过）", result["message"])
        # 被跳过的页不算在「还差几页没交」里（用户是故意跳的），其余的都交完了
        self.assertEqual([], self.api.pending())

    def test_skip_can_be_undone(self):
        self.api.skip(2)
        self.assertTrue(self.api.skipped(2))
        self.api.skip(2, False)
        self.assertFalse(self.api.skipped(2))
        self.assertEqual(5, len(self.api.pending()))

    def test_skip_existing_marks_the_pages_the_wiki_already_has(self):
        """「跳过已存在的」：维基上已经有的一律标上（年份子页在站上改过时用）。"""
        found = {"Category:重音Teto模板": True, "Template:重音Teto/2008": True,
                 "Template:重音Teto/2024": False, "Template:重音Teto/doc": False,
                 "Template:重音Teto": True}
        with mock.patch.object(ve.wiki_api, "pages_exist",
                               side_effect=lambda titles: {str(t): found[t]
                                                           for t in titles}):
            result = self.api.skip_existing()
        self.assertEqual(["Category:重音Teto模板", "Template:重音Teto/2008",
                          "Template:重音Teto"], result["skipped"])
        self.assertEqual(3, result["known"])
        flags = {page["name"]: page["skipped"] for page in self.api.pages()}
        self.assertTrue(flags["Template:重音Teto/2008"])
        self.assertFalse(flags["Template:重音Teto/2024"])
        self.assertEqual([2, 3], self.api.pending())
        with mock.patch.object(ve.wiki_api, "pages_exist",
                               side_effect=lambda titles: {str(t): found[t]
                                                           for t in titles}):
            again = self.api.skip_existing()
        self.assertEqual([], again["skipped"])          # 再点一次不再重复报

    def test_an_existing_category_page_is_never_overwritten(self):
        """分类页已经在维基上时**不建**（那页可能已经有说明 / 排序键，用户 2026-09-30）。"""
        edits = []

        def fake_edit(page, text, summary):
            edits.append(page)
            return {"ok": True}

        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page", side_effect=fake_edit), \
                mock.patch.object(ve.wiki_api, "pages_exist",
                                  side_effect=lambda titles: {
                                      str(t): str(t) == "Category:重音Teto模板"
                                      for t in titles}), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            result = self.api.submit_all("摘要")
        self.assertNotIn("Category:重音Teto模板", edits)
        self.assertEqual(4, len(edits))
        self.assertEqual("已提交 4 个页面（1 个跳过）", result["message"])
        self.assertTrue(self.api.skipped(0))

    def test_manual_submit_clears_the_skip(self):
        self.api.select(2)
        self.api.skip(2)
        with mock.patch.object(ve.login, "is_logged_in", return_value=True), \
                mock.patch.object(ve.wiki_api, "edit_page", return_value={"ok": True}), \
                mock.patch.object(ve.wiki_api, "pages_exist", side_effect=_none_exist), \
                mock.patch.object(self.api, "plan_entries", return_value=[]), \
                mock.patch.object(ve.wiki_api, "article_url", return_value="u"):
            self.api.submit("正文", "摘要")
        self.assertFalse(self.api.skipped(2))

    def test_pending_counts_the_pages_left(self):
        self.assertEqual(5, len(self.api.pending()))
        self.api.skip(0)
        self.assertEqual([1, 2, 3, 4], self.api.pending())

    def test_submit_all_without_login_fails(self):
        with mock.patch.object(ve.login, "is_logged_in", return_value=False):
            result = self.api.submit_all("摘要")
        self.assertFalse(result["ok"])
        self.assertIn("未登录", result["error"])

    def test_plan_entries_marks_songs_and_the_vocalist_page(self):
        with mock.patch.object(ve.wiki_api, "fetch_pages_text", return_value={
                "重音Teto": "{{重音Teto|state=uncollapsed|nocate=1}}\n{{VOCALOID Songbox}}",
                "2代目閻魔": "{{VOCALOID Songbox}}\n",
                "旧曲": "{{VOCALOID Songbox}}",
                "テトリス": "这是专辑页 {{Album Infobox}}"}):
            entries = {item["title"]: item for item in self.api.plan_entries()}
        # 歌姬条目上已经有模板、而且带别的参数（`|state=uncollapsed`）→ 不动
        self.assertEqual(0, entries["重音Teto"]["count"])
        self.assertEqual("已有本模板（带其它参数）", entries["重音Teto"]["kind"])
        self.assertEqual(1, entries["2代目閻魔"]["count"])        # 歌曲可以写进去
        self.assertEqual("不是歌曲条目", entries["テトリス"]["kind"])

    def test_plan_entries_offers_to_rewrite_an_old_call(self):
        """页面里已经有这个模板、但写法不对时**改写**（用户 2026-09-30）：

        主模板 / 旧年份 → 对应年份子页（拆分了才换得上年份），
        否则拆分后永远换不上年份。
        """
        with mock.patch.object(ve.wiki_api, "fetch_pages_text", return_value={
                "重音Teto": "{{重音Teto}}\n",
                "2代目閻魔": "{{重音Teto|collapsed}}\n{{VOCALOID Songbox}}",
                "旧曲": "{{重音Teto/2011|collapsed}}\n{{VOCALOID Songbox}}"}):
            entries = {item["title"]: item for item in self.api.plan_entries()}
        self.assertEqual("改写模板", entries["重音Teto"]["kind"])
        self.assertEqual(1, entries["重音Teto"]["count"])
        self.assertIn("{{重音Teto}} → {{重音Teto|nocate=1}}", entries["重音Teto"]["note"])
        self.assertEqual("改写模板", entries["2代目閻魔"]["kind"])
        self.assertIn("{{重音Teto/2024}}", entries["2代目閻魔"]["note"])
        self.assertIn("{{重音Teto/2008}}", entries["旧曲"]["note"])
        # 目标写法不带 `|collapsed`（年份子页默认就是折叠的，用户 2026-10-01）
        self.assertTrue(entries["2代目閻魔"]["note"].endswith("→ {{重音Teto/2024}}"))

    def test_plan_entries_marks_missing_pages(self):
        with mock.patch.object(ve.wiki_api, "fetch_pages_text", return_value={}):
            entries = self.api.plan_entries()
        self.assertTrue(all(item["count"] == 0 for item in entries))
        self.assertTrue(all(item["kind"] == "条目还没建" for item in entries))

    def test_plan_entries_offers_to_remove_a_leftover_call(self):
        """不再收录的曲子反过来做：把条目里残留的调用删掉（用户 2026-10-01 的 `magnet`）。"""
        self.work.skipped_covers = ["magnet"]
        with mock.patch.object(ve.wiki_api, "fetch_pages_text", return_value={
                "magnet": "{{VOCALOID Songbox}}\n{{重音Teto/2012}}\n",
                "2代目閻魔": "{{VOCALOID Songbox}}\n"}):
            entries = {item["title"]: item for item in self.api.plan_entries()}
        self.assertEqual("移除模板调用", entries["magnet"]["kind"])
        self.assertEqual(1, entries["magnet"]["count"])
        self.assertIn("{{重音Teto/2012}} → 删掉", entries["magnet"]["note"])
        self.assertEqual("加入本模板", entries["2代目閻魔"]["kind"])   # 其余照旧

    def test_a_dropped_song_without_a_call_is_greyed_out(self):
        self.work.skipped_covers = ["magnet"]
        with mock.patch.object(ve.wiki_api, "fetch_pages_text",
                               return_value={"magnet": "{{VOCALOID Songbox}}\n"}):
            entries = {item["title"]: item for item in self.api.plan_entries()}
        self.assertEqual("已无模板调用", entries["magnet"]["kind"])
        self.assertEqual(0, entries["magnet"]["count"])

    def test_fix_backlinks_groups_by_year_page(self):
        calls = []

        def fake_insert(name, titles, progress=None, call="", position="top",
                        drop_category="", rewrite=False):
            calls.append((call, list(titles), position, drop_category, rewrite))
            return [{"title": titles[0], "ok": True, "count": 1}]

        with mock.patch.object(vt, "insert_into_pages", side_effect=fake_insert):
            result = self.api.fix_backlinks(json.dumps(["2代目閻魔", "旧曲", "重音Teto"]))
        self.assertTrue(result["ok"])
        got = {tuple(call): titles for call, titles, _position, _drop, _rewrite in calls}
        self.assertEqual(["2代目閻魔"], got[("重音Teto/2024",)])
        self.assertEqual(["旧曲"], got[("重音Teto/2008",)])
        self.assertEqual(["重音Teto"], got[("重音Teto|nocate=1",)])
        # 位置：P主模板后面、活动模板前面（用户 2026-09-30）
        self.assertTrue(all(position == "after_producer"
                            for _, _, position, _, _ in calls))
        # 曲子条目要删掉手写的「重音Teto歌曲」分类；歌姬条目那份带 nocate=1，不删
        drops = {tuple(call): drop for call, _titles, _position, drop, _rewrite in calls}
        self.assertEqual("重音Teto歌曲", drops[("重音Teto/2024",)])
        self.assertEqual("", drops[("重音Teto|nocate=1",)])
        # 页面上的旧写法要改写（rewrite=True），否则拆分后换不上年份
        self.assertTrue(all(rewrite for *_, rewrite in calls))

    def test_fix_backlinks_rejects_bad_input(self):
        self.assertFalse(self.api.fix_backlinks("不是 JSON")["ok"])
        self.assertFalse(self.api.fix_backlinks("[]")["ok"])


class FlowTest(unittest.TestCase):
    def test_console_mode_writes_every_page(self):
        """没有界面（终端模式）时也要把各页面写到输出目录。"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name)
        real_specs = vt.page_specs

        def fake_specs(work):
            specs = real_specs(work)
            for index, spec in enumerate(specs):
                spec["file"] = output / f"{index}_{spec['kind']}.wikitext"
            return specs

        with mock.patch.object(ve, "ask_name", return_value="重音Teto"), \
                mock.patch.object(ve, "ask_layout", return_value=ve.LAYOUT_SPLIT), \
                mock.patch.object(ve, "ask_other_layout") as ask_other, \
                mock.patch.object(ve.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": True}), \
                mock.patch.object(vt, "prepare_work", return_value=_work()), \
                mock.patch.object(vt, "load_existing", side_effect=lambda work: work), \
                mock.patch.object(vt, "page_specs", side_effect=fake_specs), \
                mock.patch.object(ui_facade, "is_active", return_value=False), \
                mock.patch.object(ui_facade, "open_folder") as open_folder, \
                mock.patch.object(ui_facade, "status"):
            path = ve.generate_vocalist_template()
        files = sorted(item.name for item in output.iterdir())
        self.assertEqual(["0_category.wikitext", "1_year.wikitext", "2_year.wikitext",
                          "3_doc.wikitext", "4_main.wikitext"], files)
        self.assertEqual(output / "4_main.wikitext", path)
        self.assertTrue(open_folder.called)
        ask_other.assert_not_called()      # 拆分了就不问「其他栏怎么排」

    def test_single_page_mode_asks_how_to_lay_out_the_other_row(self):
        """选「不拆，一个页面写完整」时会多问一句「其他栏怎么排」，
        并把结果带到 `prepare_work(other_years=…)`。"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name)
        work = _work(split=False)
        seen = {}
        real_specs = vt.page_specs

        def fake_prepare(name, split, **kwargs):
            seen.update(kwargs)
            return work

        def fake_specs(target):
            specs = real_specs(target)
            for index, spec in enumerate(specs):
                spec["file"] = output / f"{index}_{spec['kind']}.wikitext"
            return specs

        with mock.patch.object(ve, "ask_name", return_value="弗里摩侠"), \
                mock.patch.object(ve, "ask_layout", return_value=ve.LAYOUT_ONE_PAGE), \
                mock.patch.object(ve, "ask_other_layout", return_value=True) as ask_other, \
                mock.patch.object(ve.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": False}), \
                mock.patch.object(vt, "prepare_work", side_effect=fake_prepare), \
                mock.patch.object(vt, "load_existing", side_effect=lambda item: item), \
                mock.patch.object(vt, "page_specs", side_effect=fake_specs), \
                mock.patch.object(ui_facade, "is_active", return_value=False), \
                mock.patch.object(ui_facade, "open_folder"), \
                mock.patch.object(ui_facade, "status"):
            ve.generate_vocalist_template()
        ask_other.assert_called_once()
        self.assertTrue(seen.get("other_years"))
        self.assertFalse(seen.get("subpages_only"))

    def test_subpages_only_mode_asks_for_the_flag_and_skips_the_main_template(self):
        """「只新建年份子页、不动既有主模板」：传给 prepare_work 的标记要对，
        这时没有主模板页，就打开 / 返回第一个年份子页。
        """
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name)
        work = _work()
        work.subpages_only = True
        seen = {}
        real_specs = vt.page_specs

        def fake_prepare(name, split, **kwargs):
            seen.update(kwargs)
            return work

        def fake_specs(target):
            specs = real_specs(target)
            for index, spec in enumerate(specs):
                spec["file"] = output / f"{index}_{spec['kind']}.wikitext"
            return specs

        with mock.patch.object(ve, "ask_name", return_value="初音未来"), \
                mock.patch.object(ve, "ask_layout",
                                  return_value=ve.LAYOUT_SUBPAGES_ONLY), \
                mock.patch.object(ve.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": True}), \
                mock.patch.object(vt, "prepare_work", side_effect=fake_prepare), \
                mock.patch.object(vt, "load_existing", side_effect=lambda item: item), \
                mock.patch.object(vt, "page_specs", side_effect=fake_specs), \
                mock.patch.object(ui_facade, "is_active", return_value=False), \
                mock.patch.object(ui_facade, "open_folder"), \
                mock.patch.object(ui_facade, "status"):
            path = ve.generate_vocalist_template()
        self.assertTrue(seen.get("subpages_only"))
        self.assertEqual(["0_category.wikitext", "1_year.wikitext", "2_year.wikitext"],
                         sorted(item.name for item in output.iterdir()))
        self.assertEqual(output / "1_year.wikitext", path)

    def test_giving_up_on_the_name_returns_none(self):
        with mock.patch.object(ve, "ask_name", return_value=""), \
                mock.patch.object(ui_facade, "status"):
            self.assertIsNone(ve.generate_vocalist_template())

    def test_bad_category_is_reported_and_swallowed(self):
        with mock.patch.object(ve, "ask_name", return_value="没有这个歌姬"), \
                mock.patch.object(ve, "ask_layout", return_value=ve.LAYOUT_SPLIT), \
                mock.patch.object(ve.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": False}), \
                mock.patch.object(vt, "prepare_work",
                                  side_effect=ValueError("维基上没有分类")), \
                mock.patch.object(ui_facade, "status") as status:
            self.assertIsNone(ve.generate_vocalist_template())
        self.assertTrue(any("没有分类" in str(call) for call in status.call_args_list))


if __name__ == "__main__":
    unittest.main()
