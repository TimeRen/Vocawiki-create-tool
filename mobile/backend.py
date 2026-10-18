"""Kivy 版 `utils.ui` 后端。

业务逻辑（main.generate / 各种 editor API）仍然通过 `utils.ui` 门面调用
`ask_response` / `run_style_editor` 等；这里把这些调用转成 Kivy 页面并在
后台线程上阻塞等待用户操作。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from kivy.clock import Clock

from mobile import platform


class MobileBackend:
    def __init__(self, app):
        self.app = app
        self._requests = 0
        self._active_event: Optional[threading.Event] = None
        self._active_default: Any = None
        self._active_cancelled = False

    # ------------------------------------------------------------------ 线程桥
    def _ask(self, action: Callable[[Callable[[Any], None]], None], default=None) -> Any:
        event = threading.Event()
        box: dict[str, Any] = {}
        self._active_event = event
        self._active_default = default
        self._active_cancelled = False

        def done(value=None):
            if event.is_set():
                return
            box["value"] = value
            event.set()

        def show(_dt):
            try:
                action(done)
            except Exception as e:  # noqa: BLE001 - 不能让流程线程永远等一个没打开的页面
                logging.exception("移动端页面打开失败")
                box["error"] = e
                event.set()

        Clock.schedule_once(show, 0)
        event.wait()
        cancelled = self._active_cancelled
        error = box.get("error")
        self._active_event = None
        self._active_default = None
        self._active_cancelled = False
        if cancelled:
            from utils import ui

            raise ui.RunCancelled()
        if error is not None:
            raise error
        return box.get("value", default)

    def cancel_pending(self) -> None:
        """取消当前等待中的问答（Android 返回键 / 切换功能）。"""
        self._active_cancelled = True
        event = self._active_event
        if event is not None and not event.is_set():
            event.set()

    def _later(self, action: Callable[[], None]) -> None:
        Clock.schedule_once(lambda _dt: action(), 0)

    # ------------------------------------------------------------------ 基础问答
    def ask_response(self, prompt: str, auto_strip: bool = True,
                     checker: Optional[Callable[[str], bool]] = None) -> str:
        return self._ask(lambda done: self.app.prompt_text(
            prompt, lambda value: done(value.strip() if auto_strip else value), checker),
            default="")

    def ask_choices(self, prompt: str, choices, allow_zero: bool = False) -> int:
        return int(self._ask(lambda done: self.app.prompt_choices(
            prompt, list(choices), done, allow_zero=allow_zero), default=0) or 0)

    def ask_multiline(self, prompt: str, auto_strip: bool = True, terminator=None):
        return self._ask(lambda done: self.app.prompt_multiline(
            prompt, lambda value: done(value), auto_strip=auto_strip), default=[]) or []

    # ------------------------------------------------------------------ 状态 / 日志
    def set_status(self, text: str) -> None:
        self._later(lambda: self.app.set_status(text))

    def append_log(self, text: str) -> None:
        self._later(lambda: self.app.append_log(text))

    def report_done(self, output_dir: Any = None) -> None:
        self._later(lambda: self.app.report_done(output_dir))

    def report_error(self, message: str) -> None:
        self._later(lambda: self.app.report_error(message))

    # ------------------------------------------------------------------ 样式 / 歌词 / 提交
    def run_style_editor(self, initial_wiki: str = "", cover_image: Any = None,
                         lyrics_hover: bool = False, cover_file: Any = None,
                         versions: Any = None):
        return self._ask(lambda done: self.app.show_style_editor(
            initial_wiki, cover_image, lyrics_hover, cover_file, list(versions or []), done),
            default=None)

    def run_lyrics_editor(self, api):
        return self._ask(lambda done: self.app.show_lyrics_editor(api, done), default=None)

    def run_submit_editor(self, api) -> bool:
        return bool(self._ask(lambda done: self.app.show_submit_editor(api, done), default=False))

    # ------------------------------------------------------------------ P主 / 歌姬模板
    def run_producer_works(self, work):
        return self._ask(lambda done: self.app.show_producer_works(work, done), default=None)

    def run_producer_style(self, work):
        return self._ask(lambda done: self.app.show_producer_style(work, done), default=None)

    def run_vocalist_works(self, work):
        return self._ask(lambda done: self.app.show_vocalist_works(work, done), default=None)

    def run_vocalist_style(self, payload):
        return self._ask(lambda done: self.app.show_vocalist_style(payload, done), default=None)

    # ------------------------------------------------------------------ 可选能力
    def open_folder(self, path: Any) -> bool:
        target = platform.app_dir() / "output"
        if not platform.is_android():
            try:
                import os

                os.startfile(str(target))  # type: ignore[attr-defined]
                return True
            except Exception:  # noqa: BLE001
                pass
        platform.share_file(target)
        return True


def install_backend(app) -> MobileBackend:
    backend = MobileBackend(app)
    from utils import ui

    ui._window = backend
    ui._app = None
    return backend
