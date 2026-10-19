"""Kivy 页面共用的小部件（含亮 / 暗两套配色）。

`COLORS` 是**可变**的全局字典：`apply_theme()` 就地换掉里面的颜色，之后**新建**的控件
就用新配色（Kivy 的颜色是建控件时定下来的，所以换主题要重建页面，见 `VocawikiApp`）。
"""
from __future__ import annotations

import itertools

from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import ColorProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput


_screen_ids = itertools.count(1)


THEMES = {
    "light": {
        "bg": (0.94, 0.95, 0.96, 1),
        "panel": (1, 1, 1, 1),
        "text": (0.12, 0.14, 0.18, 1),
        "muted": (0.38, 0.42, 0.48, 1),
        "accent": (0.15, 0.42, 0.72, 1),
        "accent_text": (1, 1, 1, 1),
        "danger": (0.72, 0.20, 0.20, 1),
        "ok": (0.10, 0.55, 0.35, 1),
        "border": (0.82, 0.85, 0.88, 1),
        "input_bg": (1, 1, 1, 1),
        "input_text": (0.12, 0.14, 0.18, 1),
    },
    "dark": {
        "bg": (0.08, 0.09, 0.11, 1),
        "panel": (0.15, 0.17, 0.21, 1),
        "text": (0.93, 0.94, 0.97, 1),
        "muted": (0.64, 0.68, 0.74, 1),
        "accent": (0.20, 0.48, 0.82, 1),
        "accent_text": (1, 1, 1, 1),
        "danger": (0.78, 0.28, 0.28, 1),
        "ok": (0.20, 0.70, 0.48, 1),
        "border": (0.26, 0.29, 0.34, 1),
        "input_bg": (0.19, 0.21, 0.25, 1),
        "input_text": (0.93, 0.94, 0.97, 1),
    },
}

COLORS = dict(THEMES["light"])
_theme_name = "light"


def apply_theme(name) -> str:
    """切到 `light` / `dark`（`True` / `"dark"` 都算深色），返回实际用上的名字。"""
    global _theme_name
    key = "dark" if str(name).strip().lower() in ("dark", "true", "1", "on") else "light"
    _theme_name = key
    COLORS.clear()
    COLORS.update(THEMES[key])
    return key


def theme_name() -> str:
    return _theme_name


def is_dark() -> bool:
    return _theme_name == "dark"


def _mix(color, other, ratio: float):
    """按比例把 `color` 往 `other` 上混（做禁用态 / 按下态）。"""
    return tuple(color[i] * (1 - ratio) + other[i] * ratio for i in range(3)) + (color[3],)


def _contrast(color):
    """浅底用深字、深底用浅字。"""
    r, g, b = color[0], color[1], color[2]
    return (0.10, 0.12, 0.16, 1) if (0.299 * r + 0.587 * g + 0.114 * b) > 0.6 else (1, 1, 1, 1)


class Surface(BoxLayout):
    """带圆角面板背景的容器（标题行 / 开关行 / 分组框）。"""

    bg_color = ColorProperty((1, 1, 1, 1))
    radius = NumericProperty(dp(12))

    def __init__(self, bg_color=None, radius=12, **kwargs):
        super().__init__(**kwargs)
        self.bg_color = tuple(bg_color or COLORS["panel"])
        self.radius = dp(radius)
        with self.canvas.before:
            self._color = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._sync_rect, size=self._sync_rect,
                  bg_color=self._sync_color, radius=self._sync_radius)

    def _sync_rect(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _sync_color(self, *_args):
        self._color.rgba = self.bg_color

    def _sync_radius(self, *_args):
        self._rect.radius = [self.radius]


class ThemeButton(Button):
    """自绘圆角按钮：不依赖 png 素材，亮 / 暗色都只需换颜色。"""

    bg_color = ColorProperty((0.15, 0.42, 0.72, 1))
    down_color = ColorProperty((0.10, 0.32, 0.58, 1))
    radius = NumericProperty(dp(10))

    def __init__(self, **kwargs):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        kwargs.setdefault("background_disabled_normal", "")
        kwargs.setdefault("background_color", (0, 0, 0, 0))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._color = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._sync_rect, size=self._sync_rect, bg_color=self._sync_color,
                  down_color=self._sync_down, radius=self._sync_radius,
                  state=self._sync_state, disabled=self._sync_state)

    def _sync_rect(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _sync_radius(self, *_args):
        self._rect.radius = [self.radius]

    def _sync_color(self, *_args):
        self._paint()

    def _sync_down(self, *_args):
        self._paint()

    def _sync_state(self, *_args):
        self._paint()

    def _paint(self):
        if self.disabled:
            base = _mix(self.bg_color, COLORS["bg"], 0.55)
        elif self.state == "down":
            base = self.down_color
        else:
            base = self.bg_color
        self._color.rgba = base


class ThemeInput(TextInput):
    """自绘底色的输入框：Kivy 默认的背景是 atlas 贴图，换主题时颜色不跟着走。"""

    bg_color = ColorProperty((1, 1, 1, 1))
    border_color = ColorProperty((0.8, 0.8, 0.8, 1))
    radius = NumericProperty(dp(10))

    def __init__(self, **kwargs):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_active", "")
        kwargs.setdefault("background_disabled_normal", "")
        kwargs.setdefault("background_color", (0, 0, 0, 0))
        kwargs.setdefault("border", (0, 0, 0, 0))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg = Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
            self._border_color = Color(*self.border_color)
            self._border_line = Line(rounded_rectangle=(self.x, self.y, self.width,
                                                        self.height, self.radius), width=1)
        self.bind(pos=self._sync_shape, size=self._sync_shape,
                  bg_color=self._sync_bg, border_color=self._sync_border)

    def _sync_shape(self, *_args):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border_line.rounded_rectangle = (self.x, self.y, self.width, self.height,
                                               self.radius)

    def _sync_bg(self, *_args):
        self._bg.rgba = self.bg_color

    def _sync_border(self, *_args):
        self._border_color.rgba = self.border_color


class TopBar(Surface):
    def __init__(self, title: str, on_back=None, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(54),
                         bg_color=COLORS["panel"], radius=0, padding=(dp(8), dp(6)),
                         spacing=dp(8), **kwargs)
        back = ThemeButton(text="‹", font_size=dp(26), size_hint=(None, None),
                           width=dp(44), height=dp(40),
                           bg_color=COLORS["panel"], down_color=COLORS["border"])
        back.color = COLORS["text"]
        if on_back is not None:
            back.bind(on_release=lambda *_: on_back())
        else:
            back.disabled = True
        self.add_widget(back)
        title_label = Label(text=title, halign="left", valign="middle",
                            color=COLORS["text"], font_size=dp(19), bold=True)
        title_label.bind(size=lambda widget, size: setattr(widget, "text_size", size))
        self.add_widget(title_label)


class ScreenBase(Screen):
    """带标题栏与可滚动内容区的页面。"""

    def __init__(self, app, title: str, on_back=None, **kwargs):
        # ScreenManager 用 name 切页；Kivy 的 Screen 默认 name 是空串，
        # 多个页面同名会让 current 永远停在第一页。
        kwargs.setdefault("name", f"screen-{next(_screen_ids)}")
        super().__init__(**kwargs)
        self.app = app
        self.root = BoxLayout(orientation="vertical")
        with self.canvas.before:
            self._bg_color = Color(*COLORS["bg"])
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)
        self.add_widget(self.root)
        self.root.add_widget(TopBar(title, on_back=on_back))
        self.body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10),
                              size_hint_y=None)
        self.body.bind(minimum_height=lambda box, value: setattr(box, "height", value))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        scroll.add_widget(self.body)
        self.root.add_widget(scroll)

    def _sync_bg(self, *_args):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size

    def add(self, widget):
        self.body.add_widget(widget)
        return widget


def label(text: str, color=None, size=16, **kwargs) -> Label:
    widget = Label(text=text, color=color or COLORS["text"], font_size=dp(size),
                   size_hint_y=None, halign="left", valign="middle", **kwargs)
    widget.bind(size=lambda w, size: setattr(w, "text_size", size))
    widget.bind(texture_size=lambda w, size: setattr(w, "height", max(dp(24), size[1])))
    return widget


def section(title: str) -> Label:
    """分组小标题（比正文更淡、更小）。"""
    return label(title, color=COLORS["muted"], size=14)


def button(text: str, on_release=None, color=None, variant: str = "primary",
           height: int = 46, **kwargs) -> ThemeButton:
    """按钮：`variant` 决定配色（primary 实心 / secondary 面板色 / danger 红）。

    传 `color` 时直接拿它当底色（老调用点就是这么写的）。
    """
    if color is None:
        color = COLORS["danger"] if variant == "danger" else (
            COLORS["panel"] if variant == "secondary" else COLORS["accent"])
    bg = tuple(color)
    widget = ThemeButton(text=text, size_hint_y=None, height=dp(height),
                         bg_color=bg, down_color=_mix(bg, (0, 0, 0, 1), 0.25),
                         color=kwargs.pop("text_color", _contrast(bg)), **kwargs)
    if on_release is not None:
        widget.bind(on_release=lambda *_: on_release())
    return widget


def row(*widgets, height: int = 46, spacing: int = 8) -> BoxLayout:
    """一行并排的控件（按钮组）。"""
    box = BoxLayout(size_hint_y=None, height=dp(height), spacing=dp(spacing))
    for widget in widgets:
        box.add_widget(widget)
    return box


def switch_row(title: str, switch, description: str = "") -> Surface:
    """一行「标题 + 开关」，整行一块面板 —— 以前把六个控件塞进一行，标签和开关会重叠。"""
    row_box = Surface(orientation="horizontal", size_hint_y=None,
                      height=dp(56 if description else 50),
                      bg_color=COLORS["panel"], radius=12,
                      padding=(dp(14), dp(6)), spacing=dp(10))
    texts = BoxLayout(orientation="vertical", spacing=0)
    texts.add_widget(label(title, size=15))
    if description:
        texts.add_widget(label(description, color=COLORS["muted"], size=12))
    row_box.add_widget(texts)
    switch.size_hint_x = None
    switch.width = dp(52)
    row_box.add_widget(switch)
    row_box.switch = switch
    return row_box


def text_input(text: str = "", multiline: bool = False, height: int = 44,
               grow: bool = True, **kwargs) -> TextInput:
    """输入框；`multiline` 的长文本**跟着内容长高**。

    以前固定 180dp：超出的部分只能靠在框里拖动才能看到，而框外面是 ScrollView，手指一拖
    就被外层页面滚动吃掉 —— 结果「框里的文字根本拖不动」（用户 2026-10-19 报）。
    现在整段文字都铺开，翻页交给页面本身。
    """
    kwargs.setdefault("background_normal", "")
    kwargs.setdefault("background_active", "")
    kwargs.setdefault("background_disabled_normal", "")
    kwargs.setdefault("foreground_color", COLORS["input_text"])
    kwargs.setdefault("cursor_color", COLORS["text"])
    kwargs.setdefault("hint_text_color", COLORS["muted"])
    widget = ThemeInput(text=text or "", multiline=multiline,
                        size_hint_y=None, height=dp(height),
                        font_size=dp(16), padding=(dp(10), dp(8)),
                        bg_color=COLORS["input_bg"], border_color=COLORS["border"], **kwargs)
    if multiline:
        widget.height = dp(height)
        if grow:
            widget.bind(minimum_height=lambda w, value: setattr(
                w, "height", max(dp(height), value)))
    return widget


def spacer(height: int = 8) -> BoxLayout:
    return BoxLayout(size_hint_y=None, height=dp(height))


def field(title: str, value: str = "", multiline: bool = False,
          height: int = 44) -> tuple[Label, TextInput]:
    return label(title, color=COLORS["muted"], size=14), text_input(value, multiline, height)
