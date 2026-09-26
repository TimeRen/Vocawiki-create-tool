"""侧栏 / 主题 / 图标 / 头像的单测（都在 offscreen 平台上跑，不联网）。"""
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_WEBENGINE"] = "1"

from PyQt5 import QtCore, QtGui, QtWidgets                                   # noqa: E402

from utils.ui import avatar as avatar_lib                                   # noqa: E402
from utils.ui import icons, sidebar as sidebar_lib, theme                   # noqa: E402


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _alpha(pixmap: QtGui.QPixmap, x: int, y: int) -> int:
    return pixmap.toImage().pixelColor(x, y).alpha()


def _name(pixmap: QtGui.QPixmap, x: int, y: int) -> str:
    return pixmap.toImage().pixelColor(x, y).name()


def _opaque(pixmap: QtGui.QPixmap) -> int:
    """不透明像素的个数（图标只要画出了图形就够了）。"""
    image = pixmap.toImage()
    return sum(1 for x in range(pixmap.width()) for y in range(pixmap.height())
               if image.pixelColor(x, y).alpha() > 0)


class ThemeTest(TestCase):
    def test_stylesheet_uses_palette_tokens(self):
        css = theme.stylesheet()
        self.assertIn(theme.ACCENT, css)
        self.assertIn(theme.BORDER, css)
        self.assertIn(theme.BG_PAGE, css)
        self.assertNotIn("$", css, "模板变量都要替换掉")

    def test_apply_theme_sets_style_and_palette(self):
        app = _app()
        theme.apply_theme(app)
        self.assertEqual(theme.ACCENT, app.palette().color(QtGui.QPalette.Highlight).name())
        self.assertIn(theme.ACCENT, app.styleSheet())
        self.assertEqual(theme.TEXT, app.palette().color(QtGui.QPalette.WindowText).name())

    def test_helper_styles(self):
        self.assertIn(theme.TEXT_QUIET, theme.quiet_label_style())
        self.assertIn(theme.TEXT_MUTED, theme.muted_label_style())
        self.assertIn(theme.DANGER, theme.color_style(theme.DANGER))

    def test_elide_shortens_long_text(self):
        self.assertEqual("abcdefg", theme.elide("abcdefg"))
        self.assertEqual("abc…", theme.elide("abcdefgh", 4))
        self.assertEqual("一行 文本", theme.elide("  一行\n文本  "))


class IconsTest(TestCase):
    def setUp(self):
        _app()

    def test_every_icon_draws_something(self):
        for name in ("list", "gear", "person", "logout", "refresh", "tune", "unknown"):
            pixmap = icons.pixmap(name, 20, theme.TEXT)
            self.assertFalse(pixmap.isNull(), name)
            self.assertEqual(20, pixmap.width(), name)
            self.assertGreater(_opaque(pixmap), 15, f"{name} 应该画出图形")

    def test_song_icon_is_the_character(self):
        pixmap = icons.pixmap("song", 20, theme.TEXT)
        self.assertEqual(20, pixmap.width())
        if not icons.has_fonts():
            self.skipTest("offscreen 环境没有字体，画不出汉字")
        self.assertGreater(_opaque(pixmap), 30, "「歌」字应该画出笔画")

    def test_has_fonts_matches_the_environment(self):
        # 只是保证这个探测函数本身能跑（有没有字体都行）
        self.assertIsInstance(icons.has_fonts(), bool)

    def test_gear_has_a_hole_in_the_middle(self):
        pixmap = icons.pixmap("gear", 20, theme.TEXT)
        self.assertEqual(0, _alpha(pixmap, 10, 10), "齿轮中间应该是孔")

    def test_color_is_respected(self):
        red = icons.pixmap("gear", 20, "#ff0000").toImage()
        blue = icons.pixmap("gear", 20, "#0000ff").toImage()
        found_red = any(red.pixelColor(x, y).red() > 200 for x in range(20) for y in range(20))
        found_blue = any(blue.pixelColor(x, y).blue() > 200 for x in range(20) for y in range(20))
        self.assertTrue(found_red)
        self.assertTrue(found_blue)

    def test_icon_and_brand(self):
        self.assertFalse(icons.icon("list").isNull())
        brand = icons.brand_pixmap(30)
        self.assertEqual(30, brand.width())
        self.assertGreater(_alpha(brand, 15, 15), 0)


class AvatarTest(TestCase):
    def setUp(self):
        self.app = _app()

    def test_url_points_at_avatar_extension(self):
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"):
            url = avatar_lib.avatar_url("TimeRen", 128)
        self.assertTrue(url.startswith("https://voca.wiki/extensions/Avatar/avatar.php"))
        self.assertIn("res=128", url)
        self.assertIn("user=TimeRen", url)

    def test_url_without_user_asks_for_site_default(self):
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"):
            url = avatar_lib.avatar_url("", 64)
        self.assertIn("res=64", url)
        self.assertNotIn("user=", url)

    def test_url_empty_when_origin_unknown(self):
        with mock.patch("utils.wiki_api.origin", return_value=""):
            self.assertEqual("", avatar_lib.avatar_url("TimeRen"))

    def _session_with(self, status, content_type, content):
        response = mock.Mock()
        response.status_code = status
        response.headers = {"Content-Type": content_type}
        response.content = content
        session = mock.Mock()
        session.get.return_value = response
        return session

    def test_fetch_bytes_returns_image(self):
        session = self._session_with(200, "image/png", b"\x89PNG-data")
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"), \
             mock.patch("utils.login.get_api_session", return_value=session):
            self.assertEqual(b"\x89PNG-data", avatar_lib.fetch_bytes("TimeRen", 128))

    def test_fetch_bytes_gives_up_on_cloudflare_page(self):
        session = self._session_with(403, "text/html; charset=UTF-8", b"<html>Just a moment")
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"), \
             mock.patch("utils.login.get_api_session", return_value=session):
            self.assertIsNone(avatar_lib.fetch_bytes("TimeRen", 128))

    def test_fetch_bytes_survives_network_error(self):
        session = mock.Mock()
        session.get.side_effect = OSError("没网")
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"), \
             mock.patch("utils.login.get_api_session", return_value=session):
            self.assertIsNone(avatar_lib.fetch_bytes("TimeRen", 128))

    def test_placeholder_when_logged_out_is_transparent_corner(self):
        pixmap = avatar_lib.placeholder_pixmap("", 40, False)
        self.assertEqual(40, pixmap.width())
        self.assertEqual(0, _alpha(pixmap, 1, 1), "圆形之外应该是透明的")
        self.assertGreater(_alpha(pixmap, 20, 20), 0)

    def test_placeholder_when_logged_in_uses_monogram_color(self):
        pixmap = avatar_lib.placeholder_pixmap("TimeRen", 40, True)
        self.assertEqual(avatar_lib.monogram_color("TimeRen"), _name(pixmap, 20, 20))

    def test_monogram_color_is_stable_and_from_palette(self):
        self.assertEqual(avatar_lib.monogram_color("A"), avatar_lib.monogram_color("A"))
        self.assertIn(avatar_lib.monogram_color("TimeRen"), avatar_lib.MONOGRAM_COLORS)

    def test_avatar_pixmap_falls_back_when_bytes_are_not_an_image(self):
        pixmap = avatar_lib.avatar_pixmap("TimeRen", 40, True, image_bytes=b"not an image")
        self.assertEqual(40, pixmap.width())
        self.assertEqual(avatar_lib.monogram_color("TimeRen"), _name(pixmap, 20, 20))

    def test_avatar_pixmap_uses_real_image(self):
        image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#00ff00"))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        pixmap = avatar_lib.avatar_pixmap("TimeRen", 40, True, image_bytes=bytes(buffer.data()))
        self.assertEqual(40, pixmap.width())
        self.assertEqual("#00ff00", _name(pixmap, 20, 20))

    def test_load_bytes_caches_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "avatar.img"
            with mock.patch.object(avatar_lib, "fetch_bytes", return_value=b"img") as fetch, \
                 mock.patch.object(avatar_lib, "cache_path", return_value=cache):
                avatar_lib.clear_cache()
                self.assertEqual(b"img", avatar_lib.load_bytes("缓存用户", 128))
                self.assertTrue(cache.is_file())
                avatar_lib.clear_cache()               # 清掉内存缓存，验证磁盘命中
                fetch.reset_mock()
                self.assertEqual(b"img", avatar_lib.load_bytes("缓存用户", 128))
                fetch.assert_not_called()


class SideBarTest(TestCase):
    def setUp(self):
        self.app = _app()
        self.bar = sidebar_lib.SideBar()

    def tearDown(self):
        self.bar.deleteLater()
        self.app.processEvents()

    def test_registers_the_entry_feature(self):
        self.assertEqual(["entry"], self.bar.keys())
        self.assertEqual("entry", self.bar.current_feature())
        self.assertEqual("生成歌曲条目", self.bar.label_for("entry"))

    def test_selecting_a_feature_emits(self):
        picked = []
        self.bar.feature_selected.connect(picked.append)
        self.bar._buttons[0].click()
        self.assertEqual(["entry"], picked)
        self.assertEqual("entry", self.bar.current_feature())

    def test_gear_emits_settings_requested(self):
        asked = []
        self.bar.settings_requested.connect(lambda: asked.append(True))
        self.bar.settings_button.click()
        self.assertEqual([True], asked)

    def test_avatar_emits_clicked(self):
        clicks = []
        self.bar.avatar_clicked.connect(lambda: clicks.append(True))
        self.bar.avatar_button.click()
        self.assertEqual([True], clicks)

    def test_avatar_defaults_to_logged_out(self):
        self.assertFalse(self.bar.avatar_button.logged_in)
        self.assertIn("未登录", self.bar.avatar_button.toolTip())

    def test_avatar_switches_to_user(self):
        self.bar.set_avatar("TimeRen", True)
        self.assertTrue(self.bar.avatar_button.logged_in)
        self.assertIn("TimeRen", self.bar.avatar_button.toolTip())

    def test_busy_state_disables_button(self):
        self.bar.set_avatar_busy(True, "正在登录…")
        self.assertFalse(self.bar.avatar_button.isEnabled())
        self.assertIn("正在登录", self.bar.avatar_button.toolTip())
        self.bar.set_avatar_busy(False)
        self.assertTrue(self.bar.avatar_button.isEnabled())

    def test_feature_icon_follows_selection(self):
        button = self.bar._buttons[0]
        self.assertEqual("entry", button.property("featureKey"))
        self.assertEqual("song", button.property("iconName"))
        self.assertFalse(button.icon().isNull())
