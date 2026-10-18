"""Android / 桌面共用的小型平台适配层。

Android 上优先用 jnius + android Intent；桌面调试时退回 webbrowser / 剪贴板。
所有函数都吞掉平台异常，界面不应因为某个 Intent 不可用而崩溃。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from kivy.core.clipboard import Clipboard


def is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ or sys.platform.startswith("android")


def app_dir() -> Path:
    """可写的应用私有目录（Android 上是 /data/data/<包名>/files）。"""
    configured = os.environ.get("VOCAWIKI_HOME")
    if configured:
        return Path(configured)
    try:
        from kivy.app import App

        app = App.get_running_app()
        if app is not None:
            return Path(app.user_data_dir)
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / ".vocawiki"


def ensure_app_dirs() -> None:
    for name in ("", "output", "models", "logs"):
        (app_dir() / name).mkdir(parents=True, exist_ok=True)


def _android_intent(action: str, **extras):
    from jnius import autoclass  # type: ignore

    Intent = autoclass("android.content.Intent")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    intent = Intent(action)
    for key, value in extras.items():
        if key == "type":
            intent.setType(value)
        elif key == "data":
            Uri = autoclass("android.net.Uri")
            intent.setData(Uri.parse(value))
        else:
            intent.putExtra(key, value)
    PythonActivity.mActivity.startActivity(intent)
    return intent


def open_url(url: str) -> bool:
    try:
        if is_android():
            _android_intent("android.intent.action.VIEW", data=url)
        else:
            webbrowser.open(url)
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("打开链接失败：%s", e)
        return False


def copy_text(text: str) -> None:
    Clipboard.copy(text or "")


def paste_text() -> str:
    return Clipboard.paste() or ""


def share_text(text: str, title: str = "Vocawiki") -> bool:
    try:
        if is_android():
            _android_intent("android.intent.action.SEND", type="text/plain",
                            **{"android.intent.extra.SUBJECT": title,
                               "android.intent.extra.TEXT": text})
            return True
    except Exception as e:  # noqa: BLE001
        logging.warning("分享文本失败：%s", e)
    copy_text(text)
    return False


def share_file(path: str | Path, mime: str = "application/octet-stream") -> bool:
    try:
        if is_android():
            _android_intent("android.intent.action.SEND", type=mime,
                            **{"android.intent.extra.STREAM": str(path)})
            return True
    except Exception as e:  # noqa: BLE001
        logging.warning("分享文件失败：%s", e)
    copy_text(str(path))
    return False


def _deliver(callback: Callable[[Optional[str]], None], value: Optional[str]) -> None:
    try:
        from kivy.clock import Clock

        Clock.schedule_once(lambda _dt: callback(value), 0)
    except Exception:  # noqa: BLE001
        callback(value)


_storage_permission_requested = False


def _request_storage_permission() -> None:
    global _storage_permission_requested
    if _storage_permission_requested or not is_android():
        return
    _storage_permission_requested = True
    try:
        from android.permissions import Permission, request_permissions

        permissions = [getattr(Permission, name, None) for name in
                       ("READ_MEDIA_IMAGES", "READ_EXTERNAL_STORAGE")]
        request_permissions([p for p in permissions if p])
    except Exception as e:  # noqa: BLE001 - 老系统没有 READ_MEDIA_IMAGES
        logging.debug("申请读取图片权限失败：%s", e)


def pick_file(callback: Callable[[Optional[str]], None]) -> None:
    """选择本地文件；Android 用 plyer，桌面退回 Kivy FileChooser。"""
    _request_storage_permission()
    try:
        from plyer import filechooser

        filechooser.open_file(on_selection=lambda paths: _deliver(
            callback, paths[0] if paths else None))
        return
    except Exception as e:  # noqa: BLE001
        logging.debug("plyer filechooser 不可用：%s", e)
    try:
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.filechooser import FileChooserListView
        from kivy.uix.popup import Popup

        chooser = FileChooserListView(path=str(Path.home()))
        box = BoxLayout(orientation="vertical")
        box.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height="48dp", spacing="6dp")
        popup = Popup(title="选择文件", content=box, size_hint=(0.95, 0.95))

        def choose(*_args):
            _deliver(callback, chooser.selection[0] if chooser.selection else None)
            popup.dismiss()

        buttons.add_widget(Button(text="取消", on_release=lambda *_: popup.dismiss()))
        buttons.add_widget(Button(text="选择", on_release=choose))
        box.add_widget(buttons)
        chooser.bind(on_submit=lambda _c, _p, _s: choose())
        popup.open()
    except Exception as e:  # noqa: BLE001
        logging.warning("文件选择器不可用：%s", e)
        _deliver(callback, None)


def notify(title: str, message: str) -> None:
    logging.info("%s: %s", title, message)
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=4)
    except Exception:  # noqa: BLE001
        pass


def install_exception_logger() -> None:
    """把未捕获异常写进应用目录，Android 上没有控制台可看。"""
    log_path = app_dir() / "crash.log"

    def hook(kind, value, tb):
        import traceback

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("".join(traceback.format_exception(kind, value, tb)))
            handle.write("\n")
        logging.error("未捕获异常，已写入 %s", log_path)

    sys.excepthook = hook
