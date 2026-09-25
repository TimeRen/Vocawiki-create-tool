import dataclasses
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from models.color import Color, ColorEditing, ColorScheme
from models.song import Lyrics
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
    """「歌词模板」开关要能从界面一路传回 ColorEditing。"""

    @staticmethod
    def _fake_path(exists: bool = True):
        # 界面文件在 html/ 下：joinpath(EDITOR_DIR, EDITOR_FILE)
        return mock.Mock(joinpath=lambda *args: mock.Mock(exists=lambda: exists))

    def test_hover_switch_reaches_color_editing(self):
        from utils import color_editor
        fake_webview = mock.Mock()

        def fake_start(func=None):
            func()                                    # 与 pywebview 一致：先注入初始内容，再等用户保存
            api = fake_webview.create_window.call_args.kwargs["js_api"]
            api.save("|lstyle = color: #111111;", True)

        fake_webview.start.side_effect = fake_start
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
             mock.patch.object(color_editor, "application_path", self._fake_path()):
            editing = color_editor.open_color_editor("|lbgcolor = #000000", lyrics_hover=True)
        self.assertTrue(editing.lyrics_hover)
        self.assertEqual("color: #111111;", editing.lyrics_original)
        # 初始状态通过 __vocawikiSetLyricsHover 注入给界面
        injected = [c.args[0] for c in fake_webview.create_window.return_value.evaluate_js.call_args_list]
        self.assertTrue(any("__vocawikiSetLyricsHover(true)" in text for text in injected), injected)

    def test_returns_none_when_not_saved(self):
        from utils import color_editor
        fake_webview = mock.Mock()
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
             mock.patch.object(color_editor, "application_path", self._fake_path()):
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
