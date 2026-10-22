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


def _clipboard():
    """Kivy 的剪贴板（**延迟导入**：`mobile.platform` 要能在没装 Kivy 的环境里被单测导入）。"""
    from kivy.core.clipboard import Clipboard

    return Clipboard


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
    _clipboard().copy(text or "")


def paste_text() -> str:
    return _clipboard().paste() or ""


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


def _deliver_bytes(callback, raw: Optional[bytes], name: str, error: str = "") -> None:
    """把「文件内容（或失败原因）」交回界面线程。`raw is None` 且 `error` 为空 = 用户取消。"""
    try:
        from kivy.clock import Clock

        Clock.schedule_once(lambda _dt: callback(raw, name, error), 0)
    except Exception:  # noqa: BLE001
        callback(raw, name, error)


# SAF（ACTION_OPEN_DOCUMENT）的请求码，随便取一个本应用内唯一的数
_PICK_DOCUMENT_REQUEST = 0x5643          # 'VC'
# 读文件时套的缓冲大小：逐字节读 `read()` 也能在内存里完成
_READ_CHUNK = 8192


def read_stream_bytes(stream) -> bytes:
    """把 Java 的 InputStream 整个读成 bytes —— **逐字节**用 `read()`，不碰 `byte[]`。

    2026-10-21 踩过：之前用 `stream.read(byte[])` + `ByteArrayOutputStream.toByteArray()`，
    再 `bytes(data)` 取回来。jnius 的 `byte[]` 元素是**有符号**的（-128…127），而
    `config.yaml` 里的中文注释变成 UTF-8 之后必然有 ≥ 0x80 的字节 —— `bytes(...)` 直接
    抛 `ValueError: bytes must be in range(0, 256)`，于是 APK 里导入设置文件永远失败
    （用户 2026-10-21 报）。`read()` 返回的是 0…255（EOF 是 -1），跟 Python 的 bytes 同口径。
    调用方负责给它一个缓冲过的流（`BufferedInputStream`），逐字节读才不会变成逐字节 syscall。
    """
    data = bytearray()
    while True:
        value = stream.read()
        if value is None or int(value) < 0:
            break
        data.append(int(value) & 0xFF)
    return bytes(data)


def _android_document_bytes(stream) -> bytes:
    """把 Android 给回来的输入流整份读成 bytes（外面套一层缓冲，见 `read_stream_bytes()`）。"""
    from jnius import autoclass

    buffered = autoclass("java.io.BufferedInputStream")(stream, _READ_CHUNK)
    try:
        return read_stream_bytes(buffered)
    finally:
        try:
            buffered.close()                 # 也会把这个 InputStream 一起关掉
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
                # 别当成「用户取消」：那样界面上什么都不说，用户只会觉得导入坏了
                _deliver_bytes(callback, None, "", f"读取文件失败：{e}")

        activity.bind(on_activity_result=on_activity_result)
        PythonActivity.mActivity.startActivityForResult(intent, _PICK_DOCUMENT_REQUEST)
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("打开系统文件选择器失败：%s", e)
        return False


def pick_document(callback: Callable[[Optional[bytes], str, str], None]) -> None:
    """选一个文件并读出**原始字节** → `callback(raw, 文件名, 失败原因)`。

    取消时是 `(None, "", "")`，读失败时第三个参数是给用户看的原因。
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
                _deliver_bytes(callback, None, "", f"读取文件失败：{e}")

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
                _deliver_bytes(callback, None, "", f"读取文件失败：{e}")

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


def android_api_level() -> int:
    """当前设备的 API 等级（拿不到就是 0 —— 调用方按最保守的路子走）。"""
    try:
        from jnius import autoclass

        return int(autoclass("android.os.Build$VERSION").SDK_INT)
    except Exception as e:  # noqa: BLE001 - 桌面 / 单测环境
        logging.debug("拿不到 API 等级：%s", e)
        return 0


def copy_file_into_stream(path: Path, stream) -> None:
    """把本地文件写进 Java 的 `OutputStream` —— **字节只在 Java 侧搬，不过 Python**。

    ⚠️ 别把 Python 的 bytes 直接塞给 Java 的 `Stream.write()`：Python 的 `bytes` 元素是
    0…255，Java 的 `byte[]` 是有符号的 -128…127，两边对不上（APK / 带中文的 YAML 必然有
    ≥ 0x80 的字节）。2026-10-21 就是栽在这儿：`config.yaml` 导入永远失败、APK 也装不上。
    这里先用 `java.nio.file.Files.copy`（API 26+，正好是我们的 minapi），失败再退回「读进
    Java 的 `byte[]` → 原样写出去」，全程不经过 Python 的 bytes。
    """
    from jnius import autoclass

    try:
        Files = autoclass("java.nio.file.Files")
        Paths = autoclass("java.nio.file.Paths")
        Files.copy(Paths.get(str(path)), stream)
        return
    except Exception as e:  # noqa: BLE001 - 老机型 / 某些 ROM 上 java.nio 不可用
        logging.debug("java.nio 复制不可用，改用逐块复制：%s", e)
    source = autoclass("java.io.FileInputStream")(str(path))
    try:
        buffer = autoclass("[B")(_READ_CHUNK)
        while True:
            count = source.read(buffer)
            if count is None or int(count) <= 0:
                break
            stream.write(buffer, 0, int(count))
    finally:
        try:
            source.close()
        except Exception:  # noqa: BLE001
            pass


def _downloads_dir():
    """系统的公共「下载」目录（Android 8/9 用 `_data` 写入时要用到）。"""
    from jnius import autoclass

    Environment = autoclass("android.os.Environment")
    return Path(str(Environment.getExternalStoragePublicDirectory(
        Environment.DIRECTORY_DOWNLOADS)))


# `android.provider.MediaStore.MediaColumns` 是**嵌套接口**，jnius 里只能用 `$` 取：
# 直接写 `MediaStore.MediaColumns` 会抛
# `type object 'android.provider.MediaStore' has no attribute 'MediaColumns'`
# —— 用户 2026-10-23 报的「没能直接安装（… has no attribute 'MediaColumns'）」就是它，
# 于是 APK 在手机里永远只能自己跑去发布页下载。
# 拿不到时退回下面这些**公开列名常量**（AOSP 里从 API 1 起没变过）。
MEDIA_COLUMNS = {
    "DISPLAY_NAME": "_display_name",
    "MIME_TYPE": "mime_type",
    "RELATIVE_PATH": "relative_path",
    "DATA": "_data",
    "IS_PENDING": "is_pending",
}


def media_column(name: str) -> str:
    """`MediaStore.MediaColumns.<name>` 的列名；拿不到就退回 `MEDIA_COLUMNS` 里的常量。"""
    try:
        from jnius import autoclass

        value = autoclass("android.provider.MediaStore$MediaColumns")
        column = str(getattr(value, name))
        if column:
            return column
    except Exception as e:  # noqa: BLE001 - 老机型 / jnius 版本差异都退回常量
        logging.debug("取不到 MediaStore.MediaColumns.%s：%s", name, e)
    return MEDIA_COLUMNS[name]


def _insert_apk_entry(resolver, apk: Path):
    """在「下载」里登记这个 APK，返回 `(content:// 地址, is_pending 列名或 None)`。

    Android 10+ 走 `MediaStore.Downloads`（不需要存储权限），先标 `is_pending=1`、写完再清 0；
    8/9（或 Downloads 集合插入失败时）退回 `MediaStore.Files` + `_data`（要 `WRITE_EXTERNAL_STORAGE`）。
    """
    from jnius import autoclass

    ContentValues = autoclass("android.content.ContentValues")
    Integer = autoclass("java.lang.Integer")

    def values_for(kind: str):
        values = ContentValues()
        values.put(media_column("DISPLAY_NAME"), apk.name)
        values.put(media_column("MIME_TYPE"), APK_MIME)
        if kind == "downloads":
            values.put(media_column("RELATIVE_PATH"), "Download/")
            # 必须装箱成 java.lang.Integer：`ContentValues.put` 有一堆数字重载，Python 的 int
            # 挑不出是哪一个
            values.put(media_column("IS_PENDING"), Integer(1))
        else:
            values.put(media_column("DATA"), str(_downloads_dir() / apk.name))
        return values

    attempts = []
    if android_api_level() >= 29:
        attempts.append(("downloads", autoclass("android.provider.MediaStore$Downloads")))
    attempts.append(("files", autoclass("android.provider.MediaStore$Files")))
    last_error: object = "未知原因"
    for kind, collection_class in attempts:
        try:
            collection = (collection_class.EXTERNAL_CONTENT_URI if kind == "downloads"
                          else collection_class.getContentUri("external"))
            uri = resolver.insert(collection, values_for(kind))
        except Exception as e:  # noqa: BLE001 - 这个集合不行就试下一个
            logging.info("往 %s 里登记更新包失败：%s", kind, e)
            last_error = e
            continue
        if uri is not None:
            return uri, (media_column("IS_PENDING") if kind == "downloads" else None)
        last_error = "系统没给出下载位置"
    raise OSError(f"系统不接受下载请求：{last_error}")


def publish_apk(apk: Path) -> object:
    """把 APK 放进系统「下载」目录并回一个 `content://` 地址（Android 7 起 `file://` 不给用）。

    Android 10+ 用 `MediaStore.Downloads`（不需要存储权限）；8/9 没有这个集合，退回
    `MediaStore.Files` + `_data`（要 `WRITE_EXTERNAL_STORAGE`，见 buildozer.spec）。
    """
    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    ContentValues = autoclass("android.content.ContentValues")
    Integer = autoclass("java.lang.Integer")
    activity = PythonActivity.mActivity
    resolver = activity.getContentResolver()
    uri, pending = _insert_apk_entry(resolver, apk)
    stream = resolver.openOutputStream(uri)
    if stream is None:
        raise OSError("系统没给出写入流")
    try:
        copy_file_into_stream(apk, stream)
    finally:
        stream.close()
    if pending:
        done = ContentValues()
        done.put(pending, Integer(0))
        resolver.update(uri, done, None, None)
    return uri


def can_install_packages() -> bool:
    """本应用有没有「安装未知应用」的权限（Android 8 起由用户逐应用开关）。"""
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        return bool(activity.getPackageManager().canRequestPackageInstalls())
    except Exception as e:  # noqa: BLE001 - 老系统没有这个接口 = 没有这个限制
        logging.debug("查不到「安装未知应用」开关：%s", e)
        return True


def open_install_permission_settings() -> bool:
    """直接跳到「允许安装未知应用」里本应用那一页（比让用户自己去翻设置友好）。"""
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
        intent.setData(Uri.parse("package:" + str(activity.getPackageName())))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return True
    except Exception as e:  # noqa: BLE001 - 某些 ROM 没有这一页
        logging.warning("打不开「安装未知应用」设置：%s", e)
        return False


def install_apk(apk) -> tuple[bool, str]:
    """把下载好的 APK 交给系统安装界面，返回 (是否交出去了, 给用户看的说明)。

    流程：先看「安装未知应用」有没有开（没开就跳到那一页，用户回来再点一次），然后用
    `publish_apk()` 拿到 `content://` 地址，`ACTION_VIEW` 打开系统安装器 —— 屏幕上直接
    是「是否安装这个应用」。全程不碰 `file://`（Android 7 起会抛 `FileUriExposedException`），
    也不把字节交给 Python 的 bytes（见 `copy_file_into_stream()`）。
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
        return False, "当前环境不能直接安装，请在发布页下载"
    if not can_install_packages():
        if open_install_permission_settings():
            return False, "请打开「允许安装未知应用」，返回后再点一次「检查更新」"
        return False, "系统不允许安装未知应用，请在系统设置里允许后重试"
    try:
        Intent = autoclass("android.content.Intent")
        uri = publish_apk(path)
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, APK_MIME)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        autoclass("org.kivy.android.PythonActivity").mActivity.startActivity(intent)
        return True, "已交给系统安装，按屏幕提示确认即可（config.yaml 不会被覆盖）"
    except Exception as e:                                        # noqa: BLE001 - 任何机型差异
        logging.warning("调用系统安装器失败：%s", e)
        return False, f"没能直接安装（{e}），请点「打开发布页」手动装这一次"
