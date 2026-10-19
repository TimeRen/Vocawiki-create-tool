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

def _report_startup_failure(detail: str) -> None:
    """启动阶段崩了：写进应用目录，同时打到 logcat（adb logcat 能看到）。"""
    print(detail)
    try:
        target = Path(os.environ["VOCAWIKI_HOME"]) / "crash.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(detail.rstrip() + "\n\n")
        print("startup failure written to", target)
    except Exception as e:  # noqa: BLE001 - 连日志都写不了就只能看 logcat
        print("cannot write crash.log:", e)


def run() -> None:
    """启动 Kivy 前端；import 也放在这里，导入失败才能留下堆栈。"""
    try:
        from mobile.app import VocawikiApp

        VocawikiApp().run()
    except BaseException:
        import traceback

        _report_startup_failure(traceback.format_exc())
        raise


if __name__ == "__main__":
    run()
