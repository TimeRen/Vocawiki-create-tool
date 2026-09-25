import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock
from unittest.mock import patch

from utils import submit_editor, wiki_api
from utils.family_template import FamilySync
from utils.submit_editor import CoverInfo, SubmitApi


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """记录请求参数的假会话，用于离线测试 wiki_api。"""

    def __init__(self, get_payload=None, post_payload=None):
        self.get_payload = get_payload
        self.post_payload = post_payload
        self.get_params = []
        self.post_data = []

    def get(self, url, params=None, timeout=None):
        self.get_params.append(params)
        return _FakeResponse(self.get_payload)

    def post(self, url, data=None, timeout=None):
        self.post_data.append(data)
        return _FakeResponse(self.post_payload)


def _css_page(title, content):
    return {"title": title, "revisions": [{"slots": {"main": {"content": content}}}]}


class WikiApiTest(TestCase):
    def setUp(self):
        wiki_api._site_css_cache.clear()

    def test_article_url(self):
        self.assertEqual("https://voca.wiki/wiki/%E6%AD%8C", wiki_api.article_url("歌"))
        self.assertEqual("https://voca.wiki/wiki/A_b", wiki_api.article_url("A b"))

    def test_origin(self):
        self.assertEqual("https://voca.wiki/", wiki_api.origin())

    def test_detect_skin_from_startup_script(self):
        head = ('<!DOCTYPE html><html><head><script async src="/load.php?lang=zh&amp;'
                'modules=startup&amp;only=scripts&amp;raw=1&amp;skin=citizen"></script>'
                '</head><body class="mediawiki skin-citizen skin--responsive">')
        self.assertEqual("citizen", wiki_api.detect_skin(head))

    def test_detect_skin_from_body_class(self):
        # 没有 skin= 参数时退回 body 类名，且不应把 skin--responsive 当成皮肤名
        head = '<html><head></head><body class="mediawiki ltr skin-citizen action-view skin--responsive">'
        self.assertEqual("citizen", wiki_api.detect_skin(head))

    def test_detect_skin_absent(self):
        self.assertIsNone(wiki_api.detect_skin("<html><head></head><body>"))

    def test_fetch_site_styles_skips_missing(self):
        session = _FakeSession(get_payload={"query": {"pages": [
            _css_page("MediaWiki:Common.css", ".wikitable{border:1px}"),
            {"title": "MediaWiki:Citizen.css", "missing": True},
        ]}})
        with patch("utils.wiki_api.login.get_api_session", return_value=session):
            css = wiki_api.fetch_site_styles("citizen")
        self.assertIn(".wikitable{border:1px}", css)
        self.assertIn("MediaWiki:Common.css", css)
        self.assertNotIn("Citizen.css */", css)
        self.assertIn("MediaWiki:Common.css|MediaWiki:Citizen.css", session.get_params[0]["titles"])

    def test_fetch_site_styles_cached(self):
        session = _FakeSession(get_payload={"query": {"pages": [
            _css_page("MediaWiki:Common.css", ".a{}")]}})
        with patch("utils.wiki_api.login.get_api_session", return_value=session):
            wiki_api.fetch_site_styles("citizen")
            wiki_api.fetch_site_styles("citizen")
        self.assertEqual(1, len(session.get_params))

    def test_parse_wikitext_includes_site_css(self):
        head = ('<html class="client-nojs"><head></head>'
                '<body class="mediawiki skin-citizen"><script src="/load.php?skin=citizen"></script>')
        session = _FakeSession(
            post_payload={"parse": {"text": "<p>正文</p>", "headhtml": head}},
            get_payload={"query": {"pages": [
                _css_page("MediaWiki:Common.css", ".site{color:red}")]}})
        with patch("utils.wiki_api.login.get_api_session", return_value=session):
            result = wiki_api.parse_wikitext("正文", title="测试")
        self.assertEqual("<p>正文</p>", result["html"])
        self.assertEqual(head, result["head"])
        self.assertIn(".site{color:red}", result["css"])

    def test_edit_page_requires_login(self):
        with patch("utils.wiki_api.login.is_logged_in", return_value=False):
            result = wiki_api.edit_page("标题", "正文")
        self.assertFalse(result["ok"])
        self.assertIn("未登录", result["error"])


class SubmitApiTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name).joinpath("song.wikitext")
        self.source.write_text("原始内容", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _api(self, page="中文名", ja_name="日文名", create_redirect=False,
             cover=None, family=None) -> SubmitApi:
        return SubmitApi(page, self.source, "wikitext 正文", ja_name, create_redirect, cover, family)

    def _cover(self, exists=True) -> CoverInfo:
        path = Path(self._tmp.name).joinpath("cover.jpg" if exists else "missing.jpg")
        if exists:
            path.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
        return CoverInfo(path=path, wiki_name="中文名.jpg", source_url="https://example.com",
                         authors=[], characters=["初音未来"])

    def test_preview_delegates_to_api(self):
        api = self._api()
        with patch("utils.submit_editor.wiki_api.parse_wikitext",
                   return_value={"html": "<p>x</p>"}) as parse:
            self.assertEqual({"html": "<p>x</p>"}, api.preview("some text"))
        parse.assert_called_once_with("some text", title="中文名")

    def test_save_writes_local_file(self):
        api = self._api()
        self.assertTrue(api.save("新内容")["ok"])
        self.assertEqual("新内容", self.source.read_text(encoding="utf-8"))

    def test_submit_requires_login(self):
        api = self._api()
        with patch("utils.submit_editor.login.is_logged_in", return_value=False):
            result = api.submit("内容")
        self.assertFalse(result["ok"])
        self.assertIn("未登录", result["error"])

    def test_submit_same_name_skips_redirect(self):
        # 日文原名与条目名相同 -> 不创建重定向
        api = self._api(page="同名", ja_name="同名", create_redirect=True)
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page",
                      return_value={"ok": True, "newrevid": 7}) as edit, \
                patch("utils.submit_editor.wiki_api.create_redirect") as redirect:
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertIn("已提交", result["message"])
        redirect.assert_not_called()
        edit.assert_called_once_with("同名", "正文", "摘要")
        self.assertEqual("正文", self.source.read_text(encoding="utf-8"))

    def test_submit_creates_redirect(self):
        api = self._api(page="中文名", ja_name="日文名", create_redirect=True)
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.submit_editor.wiki_api.create_redirect",
                      return_value={"ok": True}) as redirect:
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertIn("已创建重定向", result["message"])
        self.assertEqual("日文名", redirect.call_args.args[0])
        self.assertEqual("中文名", redirect.call_args.args[1])

    def test_submit_redirect_disabled_by_config(self):
        api = self._api(page="中文名", ja_name="日文名", create_redirect=False)
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.submit_editor.wiki_api.create_redirect") as redirect:
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        redirect.assert_not_called()

    def test_submit_skips_existing_redirect(self):
        api = self._api(page="中文名", ja_name="日文名", create_redirect=True)
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.submit_editor.wiki_api.create_redirect",
                      return_value={"ok": False, "exists": True}):
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertIn("已存在", result["message"])

    def test_submit_reports_error_and_keeps_local_edit(self):
        api = self._api()
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page",
                      return_value={"ok": False, "error": "boom"}):
            result = api.submit("修改后", "摘要")
        self.assertFalse(result["ok"])
        self.assertEqual("boom", result["error"])
        # 提交失败时也应保留本地修改，避免编辑内容丢失
        self.assertEqual("修改后", self.source.read_text(encoding="utf-8"))

    def test_context_reports_redirect_state(self):
        api = self._api(page="中文名", ja_name="日文名", create_redirect=True)
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.article_url", return_value="url"), \
                patch("utils.submit_editor.wiki_api.origin", return_value="origin"):
            ctx = api.get_context()
        self.assertEqual("中文名", ctx["page"])
        self.assertEqual("日文名", ctx["redirect"])
        self.assertTrue(ctx["createRedirect"])
        self.assertTrue(ctx["canSubmit"])

        same = self._api(page="同名", ja_name="同名", create_redirect=True)
        self.assertIsNone(same.get_context()["redirect"])

    def test_context_reports_cover(self):
        api = self._api(cover=self._cover())
        ctx = api.get_context()
        self.assertTrue(ctx["cover"]["exists"])
        self.assertEqual("中文名.jpg", ctx["cover"]["wikiName"])
        self.assertEqual(["初音未来"], ctx["cover"]["characters"])

        missing = self._api(cover=self._cover(exists=False))
        self.assertFalse(missing.get_context()["cover"]["exists"])

    def test_context_without_cover(self):
        self.assertIsNone(self._api().get_context()["cover"])

    def test_preview_attaches_local_cover(self):
        api = self._api(cover=self._cover())
        with patch("utils.submit_editor.wiki_api.parse_wikitext",
                   return_value={"html": "<p>x</p>", "head": "<html><head></head><body>"}):
            result = api.preview("正文")
        self.assertEqual("中文名.jpg", result["cover"]["name"])
        self.assertTrue(result["cover"]["data"].startswith("data:image/jpeg;base64,"))

    def test_preview_with_parse_error_has_no_cover(self):
        api = self._api(cover=self._cover())
        with patch("utils.submit_editor.wiki_api.parse_wikitext",
                   return_value={"error": "boom"}):
            result = api.preview("正文")
        self.assertNotIn("cover", result)

    def test_submit_uploads_cover_with_entry(self):
        api = self._api(cover=self._cover())
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.upload_image",
                      return_value={"ok": True, "message": "已上传封面「中文名.jpg」"}) as upload, \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}):
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertIn("已上传封面", result["message"])
        self.assertIn("已提交", result["message"])
        upload.assert_called_once()
        self.assertEqual("中文名.jpg", upload.call_args.args[1])
        self.assertEqual(["初音未来"], upload.call_args.kwargs["characters"])

    def test_submit_without_cover_skips_upload(self):
        api = self._api()
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.upload_image") as upload, \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}):
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        upload.assert_not_called()

    def test_submit_keeps_entry_when_cover_upload_fails(self):
        api = self._api(cover=self._cover())
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.upload_image",
                      return_value={"ok": False, "exists": True,
                                    "error": "封面「中文名.jpg」已存在，未覆盖"}), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}):
            result = api.submit("正文", "摘要")
        self.assertTrue(result["ok"])
        self.assertIn("已存在", result["message"])
        self.assertIn("已提交", result["message"])

    def test_submit_error_includes_cover_result(self):
        api = self._api(cover=self._cover())
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.upload_image",
                      return_value={"ok": True, "message": "已上传封面「中文名.jpg」"}), \
                patch("utils.submit_editor.wiki_api.edit_page",
                      return_value={"ok": False, "error": "boom"}):
            result = api.submit("正文", "摘要")
        self.assertFalse(result["ok"])
        self.assertIn("已上传封面", result["error"])
        self.assertIn("boom", result["error"])


class FamilySyncTest(TestCase):
    """提交窗口的「同步修改大家族模板」开关。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name).joinpath("song.wikitext")
        self.source.write_text("原始内容", encoding="utf-8")
        self.family = FamilySync(templates=["可不/2024"], honors=[("bilibili", 1_200_000)])

    def tearDown(self):
        self._tmp.cleanup()

    def _api(self, family=None) -> SubmitApi:
        return SubmitApi("活死人乐队", self.source, "正文", "リビングデッドバンデッド", False, None,
                         self.family if family is None else family)

    def test_context_reports_family(self):
        family = self._api().get_context()["family"]
        self.assertTrue(family["available"])
        self.assertEqual(["可不/2024"], family["templates"])
        self.assertEqual([{"site": "bilibili", "views": 1200000}], family["honors"])

    def test_context_without_family(self):
        family = SubmitApi("X", self.source, "正文").get_context()["family"]
        self.assertEqual({"available": False, "templates": [], "producers": [], "honors": [],
                          "collections": []}, family)

    def test_family_plan_is_read_only(self):
        with patch("utils.family_template.plan", return_value=["Template:可不/2024：已加入殿堂 → bilibili"]) as plan, \
                patch("utils.family_template.sync") as sync:
            result = self._api().family_plan()
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(result["lines"]))
        plan.assert_called_once()
        sync.assert_not_called()

    def test_family_plan_without_sync_info(self):
        self.assertEqual({"ok": True, "lines": []}, self._api(family=FamilySync()).family_plan())

    def test_submit_syncs_family_when_enabled(self):
        api = self._api()
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.family_template.sync",
                      return_value=["Template:可不/2024：bilibili → 已加入「殿堂 → bilibili」"]) as sync:
            result = api.submit("正文", "摘要", sync_family=True)
        self.assertTrue(result["ok"])
        self.assertIn("已提交「活死人乐队」", result["message"])
        self.assertIn("已加入", result["message"])
        sync.assert_called_once()

    def test_submit_skips_family_when_disabled(self):
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.family_template.sync") as sync:
            result = self._api().submit("正文", "摘要")
        sync.assert_not_called()
        self.assertNotIn("大家族模板", result["message"])

    def test_submit_keeps_entry_when_family_sync_fails(self):
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page", return_value={"ok": True}), \
                patch("utils.family_template.sync", side_effect=RuntimeError("boom")):
            result = self._api().submit("正文", "摘要", sync_family=True)
        self.assertTrue(result["ok"])
        self.assertIn("大家族模板同步失败", result["message"])

    def test_not_synced_when_entry_edit_fails(self):
        with patch("utils.submit_editor.login.is_logged_in", return_value=True), \
                patch("utils.submit_editor.wiki_api.edit_page",
                      return_value={"ok": False, "error": "boom"}), \
                patch("utils.family_template.sync") as sync:
            result = self._api().submit("正文", "摘要", sync_family=True)
        self.assertFalse(result["ok"])
        sync.assert_not_called()


class CloseWindowTest(TestCase):
    """提交结束后的自动关窗：前端在通知卡片倒计时 3 秒后调 close_window。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name).joinpath("song.wikitext")
        self.source.write_text("原始内容", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_closes_webview_window(self):
        api = SubmitApi("活死人乐队", self.source, "正文")
        window = mock.Mock()
        api._window = window
        self.assertEqual({"ok": True}, api.close_window())
        window.destroy.assert_called_once()

    def test_reports_when_window_unavailable(self):
        # 浏览器里调试时没有 pywebview 窗口，返回 ok=False 而不是抛异常
        result = SubmitApi("活死人乐队", self.source, "正文").close_window()
        self.assertFalse(result["ok"])
        self.assertIn("窗口不可用", result["error"])

    def test_reports_destroy_failure(self):
        api = SubmitApi("活死人乐队", self.source, "正文")
        api._window = mock.Mock()
        api._window.destroy.side_effect = RuntimeError("boom")
        result = api.close_window()
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


class ToastNoticeTest(TestCase):
    """右下角提交通知：成功才自动关窗，失败时保持窗口打开以便重试。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(submit_editor.__file__).resolve().parent.parent
                    / submit_editor.EDITOR_DIR / submit_editor.EDITOR_FILE).read_text(encoding="utf-8")

    def _func(self, name):
        """按大括号配平取出 JS 函数体，避免字符串匹配误伤其他地方。"""
        start = self.html.index("function " + name + "(")
        start = self.html.index("{", start)
        depth = 0
        for i in range(start, len(self.html)):
            if self.html[i] == "{":
                depth += 1
            elif self.html[i] == "}":
                depth -= 1
                if depth == 0:
                    return self.html[start:i + 1]
        self.fail(f"未找到函数 {name}")

    def test_countdown_is_three_seconds(self):
        self.assertIn("AUTO_CLOSE_SECONDS = 3", self.html)

    def test_show_toast_closes_window_after_countdown(self):
        body = self._func("showToast")
        self.assertIn("AUTO_CLOSE_SECONDS", body)
        self.assertIn("callApi('close_window')", body)

    def test_success_toast_auto_closes(self):
        body = self._func("finishWithToast")
        self.assertIn("showToast('ok'", body)
        self.assertIn(", true)", body)          # autoClose = true
        self.assertIn("finished = true", body)  # 关窗前不再接受新的提交

    def test_failure_toast_keeps_window_open(self):
        body = self._func("failWithToast")
        self.assertIn("showToast('err'", body)
        self.assertIn(", false,", body)                    # autoClose = false
        self.assertIn("窗口保持打开", body)
        self.assertNotIn("close_window", body)             # 失败不自动关窗

    def test_failure_reenables_submit_button(self):
        body = self._func("failWithToast")
        self.assertIn("busy = false", body)
        self.assertIn("submitBtn.disabled = false", body)

    def test_no_failure_path_uses_auto_close_toast(self):
        self.assertNotIn("finishWithToast('err'", self.html)

    def test_old_error_toasts_are_cleared_before_retry(self):
        self.assertIn("clearToasts('err')", self.html)
        self.assertIn("removeChild", self._func("clearToasts"))


class CoverFilenameTest(TestCase):
    """Songbox 的 |image 参数与上传文件名必须一致（见 main.get_cover_filename）。"""

    def test_cover_filename_is_sanitized(self):
        from types import SimpleNamespace

        import main
        self.assertEqual("初音ミク.jpg", main.get_cover_filename(SimpleNamespace(name_chs="初音ミク")))
        self.assertEqual("A_B_C.jpg", main.get_cover_filename(SimpleNamespace(name_chs="A/B:C")))
