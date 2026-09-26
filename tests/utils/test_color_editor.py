import dataclasses
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from config import config as config_module
from models.color import Color, ColorEditing, ColorScheme
from models.song import Lyrics
from utils import ai_css, color_editor
from utils.color_editor import build_initial_color_wiki, parse_color_wiki


class ParseColorWikiTest(TestCase):
    def test_parses_all_params(self):
        text = (
            "|颜色1 = #39c5bb;\n  color: #000000;\n"
            "|颜色2 = #ff0000;\n  color: #ffffff;\n"
            "|颜色3 = #123456;\n  color: #ffffff;\n"
            "|lbgcolor = #000000; border-radius: 4px\n"
            "|ltcolor = #ffffff\n"
            "|rbdcolor = #000000\n"
            "|lstyle = color: #2255ee; font-size: 14px;\n"
            "|rstyle = color: #ff8800;\n"
            "|containerstyle = background: #f5f5f5; padding: 8px;"
        )
        editing = parse_color_wiki(text)
        self.assertEqual("|颜色1 = #39c5bb;\n  color: #000000;\n"
                         "|颜色2 = #ff0000;\n  color: #ffffff;\n"
                         "|颜色3 = #123456;\n  color: #ffffff;",
                         editing.songbox)
        self.assertEqual("#000000; border-radius: 4px", editing.introduction_bg)
        self.assertEqual("#ffffff", editing.introduction_fg)
        self.assertEqual("#000000", editing.introduction_border)
        self.assertEqual("color: #2255ee; font-size: 14px;", editing.lyrics_original)
        self.assertEqual("color: #ff8800;", editing.lyrics_translated)
        self.assertEqual("background: #f5f5f5; padding: 8px;", editing.lyrics_background)

    def test_single_line_songbox_block(self):
        editing = parse_color_wiki("|颜色1 = #39c5bb; color: #000000;")
        self.assertEqual("|颜色1 = #39c5bb; color: #000000;", editing.songbox)

    def test_missing_params_fall_back_to_empty(self):
        editing = parse_color_wiki("|lbgcolor = #112233")
        self.assertEqual("", editing.songbox)
        self.assertEqual("#112233", editing.introduction_bg)
        self.assertEqual("", editing.introduction_fg)
        self.assertEqual("", editing.introduction_border)
        self.assertEqual("", editing.lyrics_original)
        self.assertEqual("", editing.lyrics_translated)
        self.assertEqual("", editing.lyrics_background)

    def test_empty_text(self):
        editing = parse_color_wiki("")
        self.assertEqual("", editing.songbox)
        self.assertEqual("", editing.introduction_bg)

    def test_ignores_other_params(self):
        # 与颜色无关的参数不应干扰解析
        editing = parse_color_wiki("|image = X.jpg\n|crop = true\n|lbgcolor = #abcdef")
        self.assertEqual("#abcdef", editing.introduction_bg)
        self.assertEqual("", editing.songbox)

    def test_hover_switch_defaults_off(self):
        self.assertFalse(parse_color_wiki("|lstyle = color: #111111;").lyrics_hover)

    def test_hover_switch_comes_from_js_argument(self):
        # 开关不在 wiki 文本里，由前端作为第二个参数回传
        editing = parse_color_wiki("|lstyle = color: #111111;", lyrics_hover=True)
        self.assertTrue(editing.lyrics_hover)
        self.assertEqual("color: #111111;", editing.lyrics_original)


class BuildInitialColorWikiTest(TestCase):
    def test_includes_template_colors(self):
        song = SimpleNamespace(colors=None)
        text = build_initial_color_wiki(song)
        # 有默认值的两项始终输出
        self.assertIn("|lbgcolor = #000000", text)
        self.assertIn("|ltcolor = #ffffff", text)
        # 默认关闭的三项不输出（关闭 = 整行省略）
        for key in ("lstyle", "rstyle", "containerstyle"):
            self.assertNotIn(f"|{key} =", text)
        self.assertNotIn("颜色1", text)

    def test_includes_songbox_colors_when_present(self):
        song = SimpleNamespace(colors=ColorScheme(background=Color(0x39, 0xc5, 0xbb),
                                                  text=Color(0, 0, 0)))
        text = build_initial_color_wiki(song)
        self.assertIn("|颜色1 = #39c5bb", text)
        self.assertIn("color: #000000;", text)
        self.assertIn("|lbgcolor = #000000", text)


class GeneratorColorWiringTest(TestCase):
    """编辑器产出的样式要分别流入 Songbox / Introduction / LyricsKai 三个模板。"""

    def test_lyrics_template_uses_editing_styles(self):
        import main
        song = SimpleNamespace(
            lyrics=Lyrics(lyrics_jap="原文", lyrics_chs="译文"),
            color_editing=ColorEditing(lyrics_original="color: #111111;",
                                       lyrics_translated="color: #222222;",
                                       lyrics_background="background: #333333;"))
        out = main.create_lyrics(song)
        self.assertIn("|lstyle=color: #111111;", out)
        self.assertIn("|rstyle=color: #222222;", out)
        self.assertIn("|containerstyle=background: #333333;", out)

    def test_lyrics_template_uses_multi_declaration_style(self):
        import main
        song = SimpleNamespace(
            lyrics=Lyrics(lyrics_jap="原文", lyrics_chs="译文"),
            color_editing=ColorEditing(lyrics_original="color: #111111; font-size: 14px;",
                                       lyrics_background="background: #333333; padding: 8px;"))
        out = main.create_lyrics(song)
        self.assertIn("|lstyle=color: #111111; font-size: 14px;", out)
        self.assertIn("|containerstyle=background: #333333; padding: 8px;", out)

    def test_song_template_uses_editing_styles(self):
        import main
        song = SimpleNamespace(
            videos=[],
            colors=None,
            creators=SimpleNamespace(staff_list=lambda: [("演唱", [])]),
            color_editing=ColorEditing(introduction_bg="#123456; border-radius: 4px",
                                       introduction_fg="#ffffff",
                                       introduction_border="#123456"))
        out = main.create_song(song)
        self.assertIn("|lbgcolor = #123456; border-radius: 4px\n", out)
        self.assertIn("|ltcolor = #ffffff\n", out)
        self.assertIn("|rbdcolor = #123456\n", out)

    def test_song_template_without_editing_keeps_default(self):
        import main
        song = SimpleNamespace(
            videos=[],
            colors=ColorScheme(background=Color(0x11, 0x22, 0x33), text=Color(255, 255, 255)),
            creators=SimpleNamespace(staff_list=lambda: [("演唱", [])]),
            color_editing=None)
        out = main.create_song(song)
        self.assertIn("|lbgcolor = #112233", out)
        self.assertIn("|ltcolor = #ffffff", out)
        self.assertNotIn("|rbdcolor", out)

    def test_lyrics_template_omits_unset_params(self):
        import main
        song = SimpleNamespace(lyrics=Lyrics(lyrics_jap="原文", lyrics_chs="译文"),
                               color_editing=None)
        out = main.create_lyrics(song)
        # 未启用颜色编辑器时不输出任何样式参数
        self.assertNotIn("|lstyle=", out)
        self.assertNotIn("|rstyle=", out)
        self.assertNotIn("|containerstyle=", out)
        self.assertIn("|original=", out)

    def test_lyrics_template_omits_disabled_color(self):
        import main
        song = SimpleNamespace(
            lyrics=Lyrics(lyrics_jap="原文", lyrics_chs="译文"),
            color_editing=ColorEditing(lyrics_original="color: #111111;",
                                       lyrics_translated="",
                                       lyrics_background="background: #333333;"))
        out = main.create_lyrics(song)
        self.assertIn("|lstyle=color: #111111;", out)
        self.assertNotIn("|rstyle=", out)                    # 关闭的整行省略
        self.assertIn("|containerstyle=background: #333333;", out)


class OpenColorEditorHoverTest(TestCase):
    """「歌词模板」开关与编辑器结果都要经过 GUI 门面（界面已是主窗口里的「样式」页）。"""

    def test_returns_none_without_gui(self):
        from utils import color_editor, ui
        with mock.patch.object(ui, "is_active", return_value=False):
            self.assertIsNone(color_editor.open_color_editor("|lbgcolor = #000000"))

    def test_delegates_to_gui_facade(self):
        from utils import color_editor, ui
        editing = ColorEditing(songbox="|颜色1 = #ffffff", lyrics_hover=True)
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_style_editor", return_value=editing) as open_style:
            result = color_editor.open_color_editor("初始", "/tmp/cover.jpg", lyrics_hover=True)
        self.assertIs(result, editing)
        open_style.assert_called_once_with("初始", "/tmp/cover.jpg", True)

    def test_returns_none_when_not_saved(self):
        from utils import color_editor, ui
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_style_editor", return_value=None):
            self.assertIsNone(color_editor.open_color_editor("|lbgcolor = #000000"))


class LyricsHoverTest(TestCase):
    """{{LyricsKai/hover}}：歌词整理窗口与颜色编辑器的开关任一开启即生效。"""

    @staticmethod
    def _song(lyrics, editing=None):
        return SimpleNamespace(lyrics=lyrics, color_editing=editing)

    def test_off_by_default(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日", lyrics_chs="中")))
        self.assertIn("{{LyricsKai\n", out)
        self.assertNotIn("/hover", out)
        self.assertNotIn("#NoHover", out)

    def test_switch_in_lyrics_window(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日", lyrics_chs="中", use_hover=True)))
        self.assertIn("{{LyricsKai/hover", out)

    def test_switch_in_color_editor(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日", lyrics_chs="中"),
                                            ColorEditing(lyrics_hover=True)))
        self.assertIn("{{LyricsKai/hover", out)

    def test_hover_marks_blank_lines(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日\n\n文", lyrics_chs="中\n\n文",
                                                  use_hover=True)))
        self.assertIn("日\n#NoHover\n文", out)

    def test_hover_drops_roma(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日", lyrics_chs="中", lyrics_roma="ri",
                                                  use_hover=True)))
        self.assertNotIn("photrans", out)
        self.assertNotIn("/Roma", out)

    def test_without_hover_roma_still_works(self):
        import main
        out = main.create_lyrics(self._song(Lyrics(lyrics_jap="日", lyrics_chs="中", lyrics_roma="ri")))
        self.assertIn("{{LyricsKai/Roma", out)
        self.assertIn("|photrans=ri", out)
        self.assertIn("{{LyricsKai/Roma/button}}", out)


class AiKeyTest(TestCase):
    """编辑器里的「API 密钥」密码输入框：写回 wiki_credentials.yaml，并回传给界面预填。"""

    CREDENTIALS = ('username: "u"\n'
                   'password: "p"\n'
                   '\n'
                   '# AI\n'
                   'ai_provider: "openai"\n'
                   'ai_model: "deepseek-flash"\n'
                   'ai_thinking: false\n'
                   'ai_api_key: ""\n')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.creds = self.root.joinpath("wiki_credentials.yaml")
        self.creds.write_text(self.CREDENTIALS, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_path(self):
        return mock.patch.object(config_module, "application_path", self.root)

    def test_save_credential_replaces_line_and_keeps_rest(self):
        with self._patch_path():
            self.assertTrue(config_module.save_credential("ai_api_key", "sk-abc"))
        text = self.creds.read_text(encoding="utf-8")
        self.assertIn('ai_api_key: "sk-abc"', text)
        self.assertIn("# AI", text)                       # 注释与其它字段都还在
        self.assertIn('password: "p"', text)
        self.assertEqual(1, text.count("ai_api_key:"))

    def test_save_credential_appends_unknown_key(self):
        with self._patch_path():
            config_module.save_credential("ai_base_url", "https://api.deepseek.com/v1")
        text = self.creds.read_text(encoding="utf-8")
        self.assertIn('ai_base_url: "https://api.deepseek.com/v1"', text)
        self.assertEqual('ai_api_key: ""', text.splitlines()[-2])

    def test_save_credential_reads_back(self):
        with self._patch_path():
            config_module.save_credential("ai_api_key", "sk-abc")
            self.assertEqual("sk-abc", config_module.get_ai_credentials()["api_key"])
            config_module.save_credential("ai_api_key", "")
            self.assertEqual("", config_module.get_ai_credentials()["api_key"])

    def test_save_ai_key_reports_enabled(self):
        api = color_editor._EditorApi()
        with self._patch_path(), \
             mock.patch.object(ai_css, "context", return_value={"enabled": True}):
            result = api.save_ai_key("  sk-abc  ")
        self.assertTrue(result["ok"])
        self.assertTrue(result["enabled"])
        self.assertIn("已保存", result["message"])
        self.assertIn('ai_api_key: "sk-abc"', self.creds.read_text(encoding="utf-8"))

    def test_save_ai_key_clears(self):
        api = color_editor._EditorApi()
        with self._patch_path(), \
             mock.patch.object(ai_css, "context", return_value={"enabled": False}):
            result = api.save_ai_key("")
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertIn("已清空", result["message"])

    def test_save_ai_key_reports_write_failure(self):
        api = color_editor._EditorApi()
        with mock.patch.object(config_module, "save_credential", return_value=False):
            result = api.save_ai_key("sk-abc")
        self.assertFalse(result["ok"])
        self.assertIn("wiki_credentials.yaml", result["error"])

    def test_context_carries_key_for_password_box(self):
        api = color_editor._EditorApi()
        with mock.patch.object(ai_css, "context", return_value={"enabled": True}), \
             mock.patch.object(ai_css, "settings", return_value={"api_key": "sk-abc"}):
            self.assertEqual("sk-abc", api.get_ai_context()["apiKey"])

    def test_context_failure_is_swallowed(self):
        api = color_editor._EditorApi()
        with mock.patch.object(ai_css, "context", side_effect=RuntimeError("boom")):
            self.assertFalse(api.get_ai_context()["enabled"])
