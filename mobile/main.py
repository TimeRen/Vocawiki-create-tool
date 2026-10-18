"""Android/Kivy 入口（Buildozer 的 android.entrypoint 指向这里）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# p4a 把源码解到应用私有目录；ANDROID_PRIVATE / ANDROID_APP_PATH 都可以作为可写根。
if "VOCAWIKI_HOME" not in os.environ:
    root = (os.environ.get("ANDROID_PRIVATE")
            or os.environ.get("ANDROID_APP_PATH")
            or str(Path.home() / ".vocawiki"))
    os.environ["VOCAWIKI_HOME"] = root

# p4a 的 site-packages 已经包含项目根；桌面调试时手动补一下。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("KIVY_NO_ARGS", "1")
# Android 上没有桌面 Chrome / Edge 可借，Cloudflare 抓取退回手动粘贴。
os.environ.setdefault("VOCAWIKI_NO_BROWSER_FETCH", "1")
# 本地 onnxruntime 模型不随 APK 打包，封面超分只走在线接口。
os.environ.setdefault("VOCAWIKI_NO_LOCAL_UPSCALE", "1")

from mobile.app import VocawikiApp  # noqa: E402


def run() -> None:
    VocawikiApp().run()


if __name__ == "__main__":
    run()
