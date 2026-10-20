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


def _deliver_bytes(callback, raw: Optional[bytes], name: str) -> None:
    try:
        from kivy.clock import Clock

        Clock.schedule_once(lambda _dt: callback(raw, name), 0)
    except Exception:  # noqa: BLE001
        callback(raw, name)


# SAF（ACTION_OPEN_DOCUMENT）的请求码，随便取一个本应用内唯一的数
_PICK_DOCUMENT_REQUEST = 0x5643          # 'VC'


def _android_document_bytes(stream) -> bytes:
    """把 Java 的 InputStream 整个读成 bytes（先试 ByteArrayOutputStream，失败再逐个字节）。"""
    from jnius import autoclass

    out = autoclass("java.io.ByteArrayOutputStream")()
    buffer = autoclass("[B")(8192)
    try:
        while True:
            count = stream.read(buffer)
            if count is None or count <= 0:
                break
            out.write(buffer, 0, int(count))
        out.flush()
        data = out.toByteArray()
        try:
            return bytes(data)                 # jnius 的 byte[] 支持 buffer 协议时最快
        except (TypeError, ValueError):
            return bytes(data[i] for i in range(len(data)))
    finally:
        try:
            out.close()
        except Exception:  # noqa: BLE001
            pass


def _android_document_name(resolver, uri) -> str:
    cursor = None
    try:
        cursor = resolver.query(uri, None, None, None, None)
        if cursor is not None and cursor.moveToFirst():
            index = cursor.getColumnIndex("_display_name")
            if index >= 0:
                return str(cursor.getString(index) or "")
    except Exception as e:  # noqa: BLE001 - 拿不到名字不影响导入
        logging.debug("读取文件名失败：%s", e)
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass
    return ""


def _android_pick_document(callback) -> bool:
    """用存储访问框架（SAF）选一个文件并读出内容 —— 不需要存储权限。

    Android 13 起 `READ_EXTERNAL_STORAGE` 只对媒体生效，直接按路径读
    `/storage/emulated/0/Download/config.yaml` 会 EACCES；SAF 由用户在系统界面里
    授权单个文件，content:// 拿到的流一定能读。
    """
    try:
        from android import activity              # p4a 提供
        from jnius import autoclass
    except Exception as e:  # noqa: BLE001 - 桌面调试时没有这两个模块
        logging.debug("SAF 不可用：%s", e)
        return False
    try:
        Intent = autoclass("android.content.Intent")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")
        state = {"finished": False}

        def on_activity_result(request_code, result_code, data):
            if request_code != _PICK_DOCUMENT_REQUEST or state["finished"]:
                return
            state["finished"] = True
            try:
                activity.unbind(on_activity_result=on_activity_result)
            except Exception:  # noqa: BLE001
                pass
            if result_code != -1 or data is None:          # -1 = RESULT_OK
                _deliver_bytes(callback, None, "")
                return
            try:
                resolver = PythonActivity.mActivity.getContentResolver()
                uri = data.getData()
                name = _android_document_name(resolver, uri)
                stream = resolver.openInputStream(uri)
                try:
                    raw = _android_document_bytes(stream)
                finally:
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                _deliver_bytes(callback, raw, name)
            except Exception as e:  # noqa: BLE001
                logging.warning("读取所选文件失败：%s", e)
                _deliver_bytes(callback, None, "")

        activity.bind(on_activity_result=on_activity_result)
        PythonActivity.mActivity.startActivityForResult(intent, _PICK_DOCUMENT_REQUEST)
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("打开系统文件选择器失败：%s", e)
        return False


def pick_document(callback: Callable[[Optional[bytes], str], None]) -> None:
    """选一个文件并读出**原始字节** → `callback(raw, 文件名)`；取消时给 `(None, "")`。

    「设置」页导入 `config.yaml` / `wiki_credentials.yaml` 用这个：内容原样拿回来，
    编码识别与校验都交给 `mobile/settings_io.py`。Android 优先走 SAF（见上），
    拿不到再退回 plyer 的文件选择器（桌面也是这条）。
    """
    if is_android() and _android_pick_document(callback):
        return
    try:
        from plyer import filechooser

        def picked(paths):
            path = paths[0] if paths else None
            if not path:
                _deliver_bytes(callback, None, "")
                return
            try:
                _deliver_bytes(callback, Path(path).read_bytes(), Path(path).name)
            except OSError as e:
                logging.warning("读不了 %s：%s", path, e)
                _deliver_bytes(callback, None, "")

        filechooser.open_file(on_selection=picked)
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
            path = chooser.selection[0] if chooser.selection else None
            popup.dismiss()
            if not path:
                _deliver_bytes(callback, None, "")
                return
            try:
                _deliver_bytes(callback, Path(path).read_bytes(), Path(path).name)
            except OSError as e:
                logging.warning("读不了 %s：%s", path, e)
                _deliver_bytes(callback, None, "")

        buttons.add_widget(Button(text="取消", on_release=lambda *_: (
            popup.dismiss(), _deliver_bytes(callback, None, ""))))
        buttons.add_widget(Button(text="选择", on_release=choose))
        box.add_widget(buttons)
        chooser.bind(on_submit=lambda _c, _p, _s: choose())
        popup.open()
    except Exception as e:  # noqa: BLE001
        logging.warning("文件选择器不可用：%s", e)
        _deliver_bytes(callback, None, "")


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


APK_MIME = "application/vnd.android.package-archive"


def install_apk(apk) -> tuple[bool, str]:
    """把下载好的 APK 交给系统安装界面，返回 (是否交出去了, 给用户看的说明)。

    Android 7 起 `file://` 会抛 `FileUriExposedException`，所以走 **MediaStore**：先把
    APK 写进系统的「下载」目录（拿到一个 `content://` 地址），再 `ACTION_VIEW` 打开它 ——
    系统会弹「是否安装」，首次还会问「允许从此来源安装应用」（那个开关只能用户自己点，
    见设置页的提示）。Android 9 以下没有 `RELATIVE_PATH` 时退回分享 / 浏览器。
    """
    path = Path(apk)
    if not path.is_file():
        return False, "更新包不见了，请重新下载"
    if not is_android():
        return False, f"桌面版请解压覆盖 exe：{path}"
    try:
        from android import activity                              # p4a 提供
        from jnius import autoclass
        del activity                                                # 只为确认 p4a 在
    except Exception as e:                                        # noqa: BLE001
        logging.warning("装 APK 需要 Android 运行时：%s", e)
        return False, "当前环境不能直接安装，已为你打开下载页"
    try:
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        ContentValues = autoclass("android.content.ContentValues")
        MediaStore = autoclass("android.provider.MediaStore")
        Downloads = autoclass("android.provider.MediaStore$Downloads")
        mActivity = PythonActivity.mActivity
        resolver = mActivity.getContentResolver()
        values = ContentValues()
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, path.name)
        values.put(MediaStore.MediaColumns.MIME_TYPE, APK_MIME)
        values.put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/")
        values.put(MediaStore.MediaColumns.IS_PENDING, 1)
        uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            raise OSError("系统没给出下载位置")
        stream = resolver.openOutputStream(uri)
        try:
            stream.write(path.read_bytes())
        finally:
            stream.close()
        values.clear()
        values.put(MediaStore.MediaColumns.IS_PENDING, 0)
        resolver.update(uri, values, None, None)
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, APK_MIME)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        mActivity.startActivity(intent)
        return True, "已交给系统安装（首次会问「允许从此来源安装应用」）"
    except Exception as e:                                        # noqa: BLE001 - 任何机型差异
        logging.warning("调用系统安装器失败：%s", e)
        return False, f"没能直接安装（{e}），已为你打开下载页"
