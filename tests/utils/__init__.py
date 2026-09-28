"""tests/utils 下的公共小工具（只放几个测试之间共用的小函数，不放夹具）。"""
import os
from pathlib import Path
from typing import Optional


def some_font_file() -> Optional[str]:
    """找一个真实存在的字体文件（「应用字体」相关的用例要用它）。

    offscreen 环境下 `QFontDatabase().families()` 是空的（没有系统字体目录），
    但**按文件**注册字体是好用的，所以字体验证一律走文件；
    这台机器上一个字体文件都没有时返回 None，调用方自己 skipTest。
    """
    root = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
    for name in ("consola.ttf", "arial.ttf", "segoeui.ttf", "calibri.ttf", "msyh.ttc"):
        candidate = root.joinpath(name)
        if candidate.exists():
            return str(candidate)
    for pattern in ("*.ttf", "*.otf", "*.ttc"):
        matches = sorted(str(path) for path in root.glob(pattern))
        if matches:
            return matches[0]
    return None
