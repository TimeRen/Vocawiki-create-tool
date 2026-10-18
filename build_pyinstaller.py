"""PyInstaller 入口：先执行正常的打包流程，再丢掉 PyQt5 自带的旧 VC 运行库。

PyQt5 的 wheel 在 `PyQt5/Qt5/bin/` 里带了一套 2020 年的 MSVCP140.dll（14.26）。
程序启动时 onnxruntime 会先加载包内较新的 MSVCP140（14.44），随后 Qt5Core 又从
自己的目录加载 14.26 —— 同一个进程里出现两个版本的 MSVCP140，窗口还没建出来就
0xC0000005 崩溃（用户 2026-10-18 报的「进程在跑但看不见界面」）。

删掉 PyQt5 目录里的这四份旧运行库后，Qt 会改用包内根目录 / 系统里的新版，
与 onnxruntime 保持一致。这里在 Analysis 结束后过滤 TOC，不修改用户环境。
"""
from PyInstaller.building import build_main

PYQT_BIN_PREFIX = "PyQt5\\Qt5\\bin\\"
RUNTIME_DLLS = {
    "MSVCP140.DLL",
    "MSVCP140_1.DLL",
    "VCRUNTIME140.DLL",
    "VCRUNTIME140_1.DLL",
}


def drop_pyqt_runtime(binaries):
    """滤掉 PyQt5 自带的 VC 运行库，保留根目录 / 系统的那一份。"""
    kept = []
    for entry in binaries:
        dest = str(entry[0]).replace("/", "\\")
        name = dest.rsplit("\\", 1)[-1].upper()
        if dest.startswith(PYQT_BIN_PREFIX) and name in RUNTIME_DLLS:
            continue
        kept.append(entry)
    return kept


_original_assemble = build_main.Analysis.assemble


def _assemble(self):
    _original_assemble(self)
    self.binaries = drop_pyqt_runtime(self.binaries)


build_main.Analysis.assemble = _assemble


if __name__ == "__main__":
    from PyInstaller.__main__ import run

    run()
