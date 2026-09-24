#!/usr/bin/env python3
"""打包脚本：用 PyInstaller 生成单文件可执行程序，并把运行时需要的资源放到 dist/ 目录。

用法:
    python build.py

完成后 dist/ 目录下包含:
    Vocawiki-create-tool[.exe]
    config.yaml
    css-tag-editor.html
    i18n/{en,zh}/LC_MESSAGES/messages.mo

这些资源运行时从可执行文件同目录读取，因此必须与 exe 放在一起。
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
EXE_NAME = "Vocawiki-create-tool"


def run(cmd):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    # 统一在项目根目录下构建，避免受调用目录影响
    os.chdir(ROOT)

    # 清理旧的构建产物
    for path in (DIST, ROOT / "build", ROOT / "main.spec"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    # 1. 用 PyInstaller 生成单文件可执行程序
    run([sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm",
         "--name", EXE_NAME, str(ROOT / "main.py")])

    # 2. 复制可编辑资源到 dist（运行时从 exe 同目录读取）
    shutil.copyfile(ROOT / "config_simple.yaml", DIST / "config.yaml")
    shutil.copyfile(ROOT / "css-tag-editor.html", DIST / "css-tag-editor.html")
    shutil.copytree(ROOT / "i18n", DIST / "i18n",
                    ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"))

    # 3. 编译 .po -> .mo
    run([sys.executable, str(ROOT / "compile_mo.py"), str(DIST / "i18n")])

    # 4. 打成 zip
    zip_name = "Windows10.zip" if sys.platform.startswith("win") else "macOS.zip"
    zip_path = ROOT / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in DIST.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT))
    print("打包完成：", zip_path, "和", DIST)


if __name__ == "__main__":
    main()
