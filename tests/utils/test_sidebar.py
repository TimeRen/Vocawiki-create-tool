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
from tests.utils import some_font_file as _some_font_file                   # noqa: E402


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

    def test_apply_theme_uses_readable_font_size(self):
        app = _app()
        theme.set_scale(1.0)
        theme.apply_theme(app)
        self.assertGreaterEqual(app.font().pointSizeF(), 10.0, "字太小看着累（Qt 默认 9pt）")
        self.assertGreaterEqual(theme.FONT_SIZE_PX, 14)
        self.assertEqual(theme.font_px(theme.MONO_SIZE_PX), theme.mono_font().pixelSize())
        # 应用级 QSS 不再写死 font-size（写死就盖掉控件 setFont，字号没法缩放）
        self.assertNotIn("font-size", theme.stylesheet())

    def test_scale_follows_window_size(self):
        """字号缩放系数跟着窗口大小走：基准窗口 = 1.0，有上下限、有量化。"""
        self.assertEqual(1.0, theme.scale_for(theme.BASE_WINDOW_W, theme.BASE_WINDOW_H))
        self.assertEqual(1.0, theme.scale_for(1090, 760), "差一点点不用变（量化）")
        self.assertGreater(theme.scale_for(1600, 1000), 1.0)
        self.assertLess(theme.scale_for(900, 620), 1.0)
        self.assertEqual(theme.SCALE_MAX, theme.scale_for(4000, 3000), "再大也不许超过上限")
        self.assertEqual(theme.SCALE_MIN, theme.scale_for(400, 300), "再小也不许低于下限")
        steps = theme.scale_for(1440, 900) / theme.SCALE_STEP
        self.assertAlmostEqual(round(steps), steps, places=6, msg="系数要量化到整档")

    def test_font_px_scales_and_keeps_a_floor(self):
        theme.set_scale(1.0)
        self.addCleanup(theme.set_scale, 1.0)
        base = theme.font_px(theme.FONT_SIZE_PX)
        theme.set_scale(1.4)
        self.assertGreater(theme.font_px(theme.FONT_SIZE_PX), base)
        theme.set_scale(0.85)
        self.assertGreaterEqual(theme.font_px(6), theme.MIN_FONT_PX, "再小也留个下限")

    def test_set_scale_reports_change(self):
        theme.set_scale(1.0)
        self.addCleanup(theme.set_scale, 1.0)
        self.assertFalse(theme.set_scale(1.0), "没变就别让调用方重套样式")
        self.assertTrue(theme.set_scale(1.2))
        self.assertTrue(theme.set_scale(9.0), "超范围会被夹到上限，但值确实变了")
        self.assertEqual(theme.SCALE_MAX, theme.scale())

    def test_rescale_reapplies_tracked_fonts(self):
        """挂过 scale_font 的控件在缩放后要拿到新字号；没挂的不动。"""
        from PyQt5 import QtWidgets
        holder = QtWidgets.QWidget()
        self.addCleanup(holder.deleteLater)
        self.addCleanup(theme.set_scale, 1.0)
        tracked = QtWidgets.QLabel("x", holder)
        plain = QtWidgets.QLabel("y", holder)
        theme.set_scale(1.0)
        theme.scale_font(tracked, theme.HISTORY_FONT_PX)
        before = tracked.font().pixelSize()
        plain_size = plain.font().pixelSize()
        theme.set_scale(1.4)
        theme.rescale(holder)
        self.assertGreater(tracked.font().pixelSize(), before)
        self.assertEqual(plain_size, plain.font().pixelSize() or plain_size,
                         "没登记的控件不该被改字号")

    def test_helper_styles(self):
        self.assertIn(theme.TEXT_QUIET, theme.quiet_label_style())
        self.assertIn(theme.TEXT_MUTED, theme.muted_label_style())
        self.assertIn(theme.DANGER, theme.color_style(theme.DANGER))

    # —— 应用字体（用户可在「设置」页换） ——

    def test_font_family_defaults_to_segoe(self):
        theme.set_font_family("")
        self.addCleanup(theme.set_font_family, "")
        self.assertEqual(theme.DEFAULT_FONT_FAMILY, theme.font_family())
        self.assertEqual(theme.FONT_STACK, theme.font_stack())

    def test_chosen_font_family_goes_first_in_the_stack(self):
        theme.set_font_family("")
        self.addCleanup(theme.set_font_family, "")
        self.assertTrue(theme.set_font_family("Some Font"))
        self.assertFalse(theme.set_font_family("Some Font"), "没变就别让调用方重套样式")
        self.assertEqual("Some Font", theme.font_family())
        self.assertEqual(f'"Some Font", {theme.FONT_STACK}', theme.font_stack())
        self.assertIn('"Some Font"', theme.stylesheet())     # 中文字体回退还在后面跟着
        self.assertIn(theme.DEFAULT_FONT_FAMILY, theme.font_stack())

    def test_chosen_font_family_reaches_the_application_font(self):
        app = _app()
        theme.set_font_family("")
        self.addCleanup(theme.set_font_family, "")
        theme.set_font_family("Some Font")
        theme.apply_theme(app)
        self.assertEqual("Some Font", app.font().family())
        theme.set_font_family("")
        theme.apply_theme(app)
        self.assertEqual(theme.DEFAULT_FONT_FAMILY, app.font().family())

    # —— 按字体文件换字体（「设置」页点输入栏选文件） ——

    def test_font_file_is_registered_and_cached(self):
        """字体文件注册进 Qt 后拿家族名当界面字体；同一个文件只注册一次。"""
        theme.apply_font("", "")
        self.addCleanup(theme.apply_font, "", "")
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        # 缓存是模块级的：先清空再测「同一个文件只注册一次」，免得被别的用例预热过
        with mock.patch.dict(theme._loaded_font_files, clear=True), \
             mock.patch.object(QtGui.QFontDatabase, "addApplicationFont",
                               side_effect=QtGui.QFontDatabase.addApplicationFont) as register:
            family = theme.load_font_file(font_file)
            self.assertEqual(family, theme.load_font_file(font_file), "同一个文件要认同一个家族名")
            self.assertEqual(1, register.call_count, "同一个文件别重复注册（Qt 里是全局的）")
        self.assertTrue(family, "字体文件里应该能读出家族名")
        self.assertTrue(theme.apply_font("", font_file))
        self.assertEqual(family, theme.font_family())
        self.assertEqual(font_file, theme.font_file())
        self.assertIn(f'"{family}"', theme.font_stack())
        self.assertFalse(theme.apply_font("", font_file), "没变就别让调用方重套样式表")

    def test_font_file_wins_over_the_family_name(self):
        """文件与 font_family 同时有值时以文件为准（用户选的字体可能没装进系统）。"""
        theme.apply_font("", "")
        self.addCleanup(theme.apply_font, "", "")
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        theme.apply_font("Some Font", font_file)
        self.assertEqual(theme.load_font_file(font_file), theme.font_family())
        # 用户把文件删了 / 挪走了 → 退回按 font_family 找系统字体，别把界面卡死
        theme.apply_font("Some Font", str(Path(tempfile.gettempdir()) / "no-such-font.ttf"))
        self.assertEqual("Some Font", theme.font_family())

    def test_set_font_family_clears_the_file(self):
        """按名字换字体要把之前选的字体文件清掉，否则文件会一直盖着这个名字。"""
        theme.apply_font("", "")
        self.addCleanup(theme.apply_font, "", "")
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        theme.apply_font("", font_file)
        theme.set_font_family("Some Font")
        self.assertEqual("", theme.font_file())
        self.assertEqual("Some Font", theme.font_family())

    def test_checkbox_check_mark_is_a_real_image(self):
        """蓝底白对号：QSS 重画了 indicator 就得自己给一张对号图，且文件要真的存在。"""
        import re as _re
        css = theme.stylesheet()
        self.assertIn("QCheckBox::indicator:checked", css)
        rule = css.split("QCheckBox::indicator:checked {")[1].split("}")[0]
        self.assertIn("image: url(", rule)
        self.assertIn(theme.ACCENT, rule, "选中还是蓝底")
        match = _re.search(r"image: url\(([^)]+)\)", rule)
        self.assertTrue(Path(match.group(1)).exists(), "对号图必须真的画出来了")
        # 禁用但还是勾着时也要看得见（底色换成深灰）
        disabled = css.split("QCheckBox::indicator:checked:disabled,")[1].split("}")[0]
        self.assertIn(theme.BORDER_STRONG, disabled)

    def test_checkbox_rules_do_not_leave_the_plain_indicator_blue(self):
        css = theme.stylesheet()
        plain = css.split("QCheckBox::indicator {")[1].split("}")[0]
        self.assertNotIn(theme.ACCENT, plain, "未选中的框还是白底")

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
        # 取圆内**左侧**的像素：正中间（20, 20）会被白色的首字母盖住，
        # 而首字母画不画得出来取决于当时 Qt 里有没有可用字体（例如别的用例刚注册过字体文件），
        # 会让这个用例随测试顺序变红。
        self.assertEqual(avatar_lib.monogram_color("TimeRen"), _name(pixmap, 6, 20))

    def test_monogram_color_is_stable_and_from_palette(self):
        self.assertEqual(avatar_lib.monogram_color("A"), avatar_lib.monogram_color("A"))
        self.assertIn(avatar_lib.monogram_color("TimeRen"), avatar_lib.MONOGRAM_COLORS)

    def test_avatar_pixmap_falls_back_when_bytes_are_not_an_image(self):
        pixmap = avatar_lib.avatar_pixmap("TimeRen", 40, True, image_bytes=b"not an image")
        self.assertEqual(40, pixmap.width())
        # 同上：避开正中间的首字母，只看圆内的底色
        self.assertEqual(avatar_lib.monogram_color("TimeRen"), _name(pixmap, 6, 20))

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

    # —— 按用户 ID 拼地址 + 借 WebEngine 取图（2026-09 用户转来的建议） ——

    def test_avatar_file_url_uses_the_user_id(self):
        """头像文件是**按用户 ID** 存的：/images/avatars/<id>/<size>.png（实测确认）。"""
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"):
            self.assertEqual("https://voca.wiki/images/avatars/35/128.png",
                             avatar_lib.avatar_file_url("35", 128))
            self.assertEqual("https://voca.wiki/images/avatars/35/64.png",
                             avatar_lib.avatar_file_url(35, 64))
            self.assertEqual("", avatar_lib.avatar_file_url("", 128))
        with mock.patch("utils.wiki_api.origin", return_value=""):
            self.assertEqual("", avatar_lib.avatar_file_url("35", 128))

    def test_wiki_user_id_asks_the_api(self):
        session = mock.Mock()
        session.get.return_value.json.return_value = {"query": {"users": [{"userid": 35}]}}
        with mock.patch("utils.login.get_api_session", return_value=session), \
             mock.patch("utils.login.api_url", return_value="https://voca.wiki/api.php"):
            avatar_lib._USER_IDS.clear()
            self.assertEqual("35", avatar_lib.wiki_user_id("AdorN"))
            self.assertEqual("35", avatar_lib.wiki_user_id("AdorN"), "查过就记下来")
        session.get.assert_called_once()
        self.assertEqual("AdorN", session.get.call_args.kwargs["params"]["ususers"])

    def test_wiki_user_id_survives_failure(self):
        session = mock.Mock()
        session.get.side_effect = OSError("没网")
        with mock.patch("utils.login.get_api_session", return_value=session), \
             mock.patch("utils.login.api_url", return_value="https://voca.wiki/api.php"):
            avatar_lib._USER_IDS.clear()
            self.assertEqual("", avatar_lib.wiki_user_id("AdorN"))
            self.assertEqual("", avatar_lib.wiki_user_id(""))

    def test_login_name_suffix_is_not_sent_to_the_site(self):
        """登录名里的 `@机器人名` 后缀不能带去查头像（用户 2026-09 报头像出不来）。

        实测：`ususers=人间百态@create` → missing、`avatar.php?user=人间百态@create`
        → 302 到站点默认头像（那张图在 Cloudflare 后面，403 下不来）；
        去掉后缀的「人间百态」才是 userid 28，有真头像。
        """
        with mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"):
            url = avatar_lib.avatar_url("人间百态@create", 128)
        self.assertIn("user=%E4%BA%BA%E9%97%B4%E7%99%BE%E6%80%81", url)
        self.assertNotIn("%40", url, "后缀不能带进请求")

    def test_wiki_user_id_looks_up_the_site_name(self):
        session = mock.Mock()
        session.get.return_value.json.return_value = {"query": {"users": [{"userid": 28}]}}
        with mock.patch("utils.login.get_api_session", return_value=session), \
             mock.patch("utils.login.api_url", return_value="https://voca.wiki/api.php"):
            avatar_lib._USER_IDS.clear()
            self.assertEqual("28", avatar_lib.wiki_user_id("人间百态@create"))
        self.assertEqual("人间百态", session.get.call_args.kwargs["params"]["ususers"])

    def test_fetch_avatar_prefers_cache_and_falls_back_to_the_id_url(self):
        """缓存里有图就直接用；没有就给个「按 ID 拼出来的地址」，交给 WebEngine 去取。"""
        with mock.patch.object(avatar_lib, "load_bytes", return_value=b"cached"):
            self.assertEqual((b"cached", ""), avatar_lib.fetch_avatar("TimeRen"))
        with mock.patch.object(avatar_lib, "load_bytes", return_value=None), \
             mock.patch.object(avatar_lib, "wiki_user_id", return_value="35"), \
             mock.patch("utils.wiki_api.origin", return_value="https://voca.wiki"):
            self.assertEqual((None, "https://voca.wiki/images/avatars/35/128.png"),
                             avatar_lib.fetch_avatar("TimeRen"))
        with mock.patch.object(avatar_lib, "load_bytes", return_value=None), \
             mock.patch.object(avatar_lib, "wiki_user_id", return_value=""):
            self.assertEqual((None, ""), avatar_lib.fetch_avatar("TimeRen"))

    def test_store_bytes_fills_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "avatar.img"
            with mock.patch.object(avatar_lib, "cache_path", return_value=cache), \
                 mock.patch.object(avatar_lib, "fetch_bytes") as fetch:
                avatar_lib.clear_cache()
                avatar_lib.store_bytes("缓存用户", 128, b"from-webengine")
                self.assertEqual(b"from-webengine", avatar_lib.load_bytes("缓存用户", 128))
                fetch.assert_not_called()
            avatar_lib.clear_cache()

    def test_webengine_loader_is_unavailable_in_tests(self):
        """单测/终端模式设了 VOCAWIKI_NO_WEBENGINE：不许去起 Chromium。"""
        self.assertFalse(avatar_lib.WebEngineLoader.available())


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

    def test_placeholder_avatar_explains_itself(self):
        """取不到站点头像时鼠标提示要说清原因（2026-09 实测：图片被 Cloudflare 挡）。"""
        self.bar.set_avatar("TimeRen", True)                 # 没有图片字节 = 用的是首字母色块
        tooltip = self.bar.avatar_button.toolTip()
        self.assertIn("没取到站点头像图片", tooltip)
        self.assertIn("Cloudflare", tooltip)
        # 未登录那张是本地画的，别写成「站点默认头像」
        self.bar.set_avatar("", False)
        self.assertNotIn("站点默认头像", self.bar.avatar_button.toolTip())
        self.assertIn("本地画", self.bar.avatar_button.toolTip())

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
