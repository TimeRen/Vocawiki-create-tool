"""Kivy 页面共用的小部件。"""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput


COLORS = {
    "bg": (0.97, 0.97, 0.98, 1),
    "panel": (1, 1, 1, 1),
    "text": (0.12, 0.14, 0.18, 1),
    "muted": (0.38, 0.42, 0.48, 1),
    "accent": (0.18, 0.48, 0.78, 1),
    "danger": (0.78, 0.18, 0.18, 1),
    "ok": (0.10, 0.55, 0.35, 1),
}


class TopBar(BoxLayout):
    def __init__(self, title: str, on_back=None, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(52), **kwargs)
        self.padding = (dp(6), dp(4))
        self.spacing = dp(6)
        back = Button(text="‹", font_size=dp(26), size_hint_x=None, width=dp(46))
        if on_back is not None:
            back.bind(on_release=lambda *_: on_back())
        else:
            back.disabled = True
        self.add_widget(back)
        label = Label(text=title, halign="left", valign="middle", color=COLORS["text"],
                      font_size=dp(18), bold=True)
        label.bind(size=lambda widget, size: setattr(widget, "text_size", size))
        self.add_widget(label)


class ScreenBase(Screen):
    """带标题栏与可滚动内容区的页面。"""

    def __init__(self, app, title: str, on_back=None, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.root = BoxLayout(orientation="vertical")
        self.add_widget(self.root)
        self.root.add_widget(TopBar(title, on_back=on_back))
        self.body = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(self.body)
        self.root.add_widget(scroll)

    def add(self, widget):
        self.body.add_widget(widget)
        return widget


def label(text: str, color=None, size=16, **kwargs) -> Label:
    widget = Label(text=text, color=color or COLORS["text"], font_size=dp(size),
                   size_hint_y=None, halign="left", valign="middle", **kwargs)
    widget.bind(size=lambda w, size: setattr(w, "text_size", size))
    widget.bind(texture_size=lambda w, size: setattr(w, "height", max(dp(24), size[1])))
    return widget


def button(text: str, on_release=None, color=None, **kwargs) -> Button:
    widget = Button(text=text, size_hint_y=None, height=dp(44),
                    background_color=color or COLORS["accent"], **kwargs)
    if on_release is not None:
        widget.bind(on_release=lambda *_: on_release())
    return widget


def text_input(text: str = "", multiline: bool = False, height: int = 44, **kwargs) -> TextInput:
    widget = TextInput(text=text or "", multiline=multiline,
                       size_hint_y=None, height=dp(height),
                       font_size=dp(16), **kwargs)
    if multiline:
        widget.height = dp(height)
    return widget


def spacer(height: int = 8) -> BoxLayout:
    return BoxLayout(size_hint_y=None, height=dp(height))


def field(title: str, value: str = "", multiline: bool = False,
          height: int = 44) -> tuple[Label, TextInput]:
    return label(title, color=COLORS["muted"], size=14), text_input(value, multiline, height)
