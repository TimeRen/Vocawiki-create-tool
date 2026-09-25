#!/usr/bin/env python3
"""打包脚本：用 PyInstaller 生成单文件可执行程序，并把运行时需要的资源放到 dist/ 目录。

用法:
    python build.py            # 运行中会在终端询问版本号
    python build.py 1.0.0      # 直接指定版本号（跳过询问）

完成后 dist/ 目录下包含:
    Vocawiki-create-tool[.exe]
    config.yaml
    html/（颜色编辑器 / 提交窗口 / 歌词整理窗口）
    wiki_credentials.yaml
    i18n/{en,zh}/LC_MESSAGES/messages.mo

这些资源运行时从可执行文件同目录读取，因此必须与 exe 放在一起。
项目根目录下同时生成发布包 Vocawiki-create-tool (版本号).zip。
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
EXE_NAME = "Vocawiki-create-tool"

# 打包时要清空的字段：密码与 AI 密钥绝不能进分发包
SECRET_KEYS = ("username", "password", "ai_api_key")


def run(cmd):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def write_credentials_template(target: Path) -> None:
    """把本地凭据文件里的密钥清空后写入 dist（保留注释与结构，避免泄露账号与 AI key）。"""
    source = ROOT / "wiki_credentials.yaml"
    text = source.read_text(encoding="utf-8") if source.exists() else 'username: ""\npassword: ""\n'
    for key in SECRET_KEYS:
        text = re.sub(rf'(?m)^{key}\s*:.*$', f'{key}: ""', text)
    if "ai_api_key" not in text:                      # 旧模板没有 AI 段时补上
        text = text.rstrip() + ('\n\nai_provider: "openai"\n'
                                'ai_base_url: "https://api.deepseek.com/v1"\n'
                                'ai_model: "deepseek-flash"\n'
                                'ai_api_key: ""\n'
                                'ai_thinking: false\n')
    target.write_text(text, encoding="utf-8")


def ask_version(argv) -> str:
    """获取版本号：命令行参数优先，否则在终端询问（直接回车则用 0.0.0）。"""
    version = argv[1].strip() if len(argv) > 1 else ""
    if not version:
        try:
            version = input("请输入本次发布的版本号（例如 1.0.0，直接回车用 0.0.0）: ").strip()
        except EOFError:                              # 非交互式环境（CI / 管道）
            version = ""
    if not version:
        version = "0.0.0"
        print("未输入版本号，使用 0.0.0")
    return re.sub(r'[<>:"/\\|?*]', "_", version)     # 文件名非法字符换成下划线


def zip_name_for(version: str) -> str:
    """发布包文件名：Vocawiki-create-tool (版本号).zip"""
    return f"{EXE_NAME} ({version}).zip"


def main():
    # 统一在项目根目录下构建，避免受调用目录影响
    os.chdir(ROOT)

    version = ask_version(sys.argv)                   # 先问版本号，再开始耗时的打包

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
    # 界面文件夹：html/ 下是颜色编辑器 / 提交窗口 / 歌词整理窗口
    shutil.copytree(ROOT / "html", DIST / "html")
    write_credentials_template(DIST / "wiki_credentials.yaml")
    shutil.copytree(ROOT / "i18n", DIST / "i18n",
                    ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"))

    # 3. 编译 .po -> .mo
    run([sys.executable, str(ROOT / "compile_mo.py"), str(DIST / "i18n")])

    # 4. 打成 zip：Vocawiki-create-tool (版本号).zip
    zip_name = zip_name_for(version)
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
