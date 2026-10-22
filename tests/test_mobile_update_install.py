"""APK 自我更新最后一步：把更新包交给系统安装器（`mobile/platform.py` 的 `publish_apk()`）。

用户 2026-10-23 报：手机上点「检查更新」，下载完弹出一句
`没能直接安装（type object 'android.provider.MediaStore' has no attribute 'MediaColumns'）`，
只能自己跑去发布页下载。

原因是 jnius 里**嵌套接口必须用 `$`**：`MediaStore.MediaColumns` 取不到（那不是一个属性），
正确的写法是 `autoclass("android.provider.MediaStore$MediaColumns")`。
这里用一个「和 jnius 一样不认 `MediaStore.MediaColumns`」的假 jnius 把这条路跑一遍，
确保以后再改也不会退回那条死路（不需要真机、不需要 Kivy）。
"""
import sys
import tempfile
import types
from pathlib import Path
from unittest import TestCase, mock

from mobile import platform


class FakeContentValues:
    """`android.content.ContentValues`：只记下 put 了什么。"""

    def __init__(self):
        self.values = {}

    def put(self, key, value):
        self.values[str(key)] = value


class FakeUri:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


class FakeStream:
    def __init__(self):
        self.closed = False
        self.written = b""

    def close(self):
        self.closed = True


class FakeResolver:
    def __init__(self, fail_kinds=()):
        self.inserted = []          # [(collection, values)]
        self.updated = []           # [(uri, values)]
        self.fail_kinds = set(fail_kinds)

    def insert(self, collection, values):
        if collection.kind in self.fail_kinds:
            raise OSError(f"insert 失败：{collection.kind}")
        self.inserted.append((collection, values))
        return FakeUri(f"content://{collection.kind}/{len(self.inserted)}.apk")

    def openOutputStream(self, uri):                        # noqa: N802 - Java 方法名
        self.stream = FakeStream()
        return self.stream

    def update(self, uri, values, *_args):
        self.updated.append((uri, values))
        return 1


class FakeCollection:
    def __init__(self, kind):
        self.kind = kind
        self.EXTERNAL_CONTENT_URI = self

    def getContentUri(self, volume):                        # noqa: N802 - Java 方法名
        return FakeCollection(f"{self.kind}:{volume}")


class FakeDownloads(FakeCollection):
    def __init__(self):
        super().__init__("downloads")
        self.EXTERNAL_CONTENT_URI = FakeCollection("downloads")


class FakeMediaColumns:
    DISPLAY_NAME = "_display_name"
    MIME_TYPE = "mime_type"
    RELATIVE_PATH = "relative_path"
    DATA = "_data"
    IS_PENDING = "is_pending"

    def __getattr__(self, name):                        # 未知列名：就当 jnius 取不到
        raise AttributeError(name)


class FakeMediaStore:
    """**故意不提供 `MediaColumns`** —— 真 jnius 拿到的就是这样（用户报的那次错）。"""

    @property
    def MediaColumns(self):                             # noqa: N802 - 模仿 Java 的写法
        raise AttributeError("MediaColumns")


class FakeInteger(int):
    pass


def _fake_jnius(api_level: int):
    """造一个假 jnius：`autoclass` 只认 `$` 形式的嵌套类，不认 `A.B` 写法。"""

    classes = {
        "android.os.Build$VERSION": types.SimpleNamespace(SDK_INT=api_level),
        "android.provider.MediaStore": FakeMediaStore(),
        "android.provider.MediaStore$MediaColumns": FakeMediaColumns(),
        "android.provider.MediaStore$Downloads": FakeDownloads(),
        "android.provider.MediaStore$Files": FakeCollection("files"),
        "android.content.ContentValues": FakeContentValues,
        "java.lang.Integer": FakeInteger,
        "org.kivy.android.PythonActivity": types.SimpleNamespace(mActivity=None),
        "android.os.Environment": types.SimpleNamespace(
            DIRECTORY_DOWNLOADS="Download",
            getExternalStoragePublicDirectory=lambda _name: "/storage/emulated/0/Download"),
    }

    def autoclass(name):
        if name not in classes:
            raise ValueError(f"no class {name}")
        return classes[name]

    module = types.ModuleType("jnius")
    module.autoclass = autoclass
    module.__dict__["__all__"] = ["autoclass"]
    return module


class PublishApkTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.apk = Path(self._tmp.name) / "Vocawiki-create-tool-latest.apk"
        self.apk.write_bytes(b"PK\x03\x04 fake apk")
        self.resolver = FakeResolver()
        activity = types.SimpleNamespace(getContentResolver=lambda: self.resolver)
        self._patch_jnius(api_level=34)
        import_mock = mock.patch.object(
            platform, "copy_file_into_stream", lambda path, stream: None)
        import_mock.start()
        self.addCleanup(import_mock.stop)

    def _patch_jnius(self, api_level: int):
        module = _fake_jnius(api_level)
        python_activity = module.autoclass("org.kivy.android.PythonActivity")
        python_activity.mActivity = types.SimpleNamespace(
            getContentResolver=lambda: self.resolver)
        patcher = mock.patch.dict(sys.modules, {"jnius": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_downloads_collection_is_used_on_android_10_and_up(self):
        uri = platform.publish_apk(self.apk)
        self.assertIn("downloads", str(uri))
        collection, values = self.resolver.inserted[0]
        self.assertEqual("downloads", collection.kind)
        self.assertEqual("Vocawiki-create-tool-latest.apk", values.values["_display_name"])
        self.assertEqual(platform.APK_MIME, values.values["mime_type"])
        self.assertEqual("Download/", values.values["relative_path"])
        self.assertEqual(1, values.values["is_pending"])
        # 写完要把 is_pending 清成 0，否则文件在「下载」里看不见
        self.assertEqual(0, self.resolver.updated[0][1].values["is_pending"])

    def test_the_media_columns_nested_interface_is_reachable(self):
        """回归：`MediaStore.MediaColumns` 取不到时不能整条路都断（用户 2026-10-23 报的）。"""
        self.assertEqual("_display_name", platform.media_column("DISPLAY_NAME"))
        self.assertEqual("is_pending", platform.media_column("IS_PENDING"))

    def test_a_missing_nested_class_falls_back_to_the_column_names(self):
        module = types.ModuleType("jnius")

        def autoclass(name):
            raise ValueError(f"no class {name}")

        module.autoclass = autoclass
        with mock.patch.dict(sys.modules, {"jnius": module}):
            self.assertEqual("_display_name", platform.media_column("DISPLAY_NAME"))
            self.assertEqual("relative_path", platform.media_column("RELATIVE_PATH"))

    def test_android_9_uses_the_files_collection(self):
        self._patch_jnius(api_level=28)
        platform.publish_apk(self.apk)
        collection, values = self.resolver.inserted[0]
        self.assertEqual("files:external", collection.kind)
        self.assertIn("_data", values.values)
        self.assertNotIn("is_pending", values.values)

    def test_downloads_insert_failure_falls_back_to_files(self):
        self.resolver.fail_kinds = {"downloads"}
        self._patch_jnius(api_level=34)
        platform.publish_apk(self.apk)
        collection, _values = self.resolver.inserted[0]
        self.assertEqual("files:external", collection.kind)

    def test_a_resolver_that_refuses_everything_reports_a_readable_error(self):
        self.resolver.fail_kinds = {"downloads", "files", "files:external"}
        self._patch_jnius(api_level=34)
        with self.assertRaises(OSError) as caught:
            platform.publish_apk(self.apk)
        self.assertIn("不接受下载请求", str(caught.exception))
