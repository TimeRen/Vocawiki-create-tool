#!/usr/bin/env python3
"""打包脚本：用 PyInstaller 生成单文件可执行程序，并把运行时需要的资源放到 dist/ 目录。

用法:
    python build.py            # 运行中会在终端询问版本号
    python build.py 1.0.0      # 直接指定版本号（跳过询问）

可执行文件的图标：把图标图片存成 assets/icon.png（正方形最好），打包时会自动转出
多尺寸的 assets/icon.ico 并用 --icon 嵌进 exe（Windows）；只放 assets/icon.ico 也可以。
两处都没有时不带图标打包，只在终端提醒一句。

完成后 dist/ 目录下包含:
    Vocawiki-create-tool[.exe]
    config.yaml
    wiki_credentials.yaml
    i18n/{en,zh}/LC_MESSAGES/messages.mo

这些资源运行时从可执行文件同目录读取，因此必须与 exe 放在一起。
界面是 PyQt5 写的主窗口（打包成窗口程序，双击 exe 会直接打开），
预览用 PyQtWebEngine。项目根目录下同时生成发布包 Vocawiki-create-tool (版本号).zip。
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
EXE_NAME = "Vocawiki-create-tool"

# 打包时要清空的字段：密码与 AI 密钥绝不能进分发包
SECRET_KEYS = ("username", "password", "ai_api_key")

# 图标：assets/ 下叫 icon 的那张图片就是图标（png 优先后备其它格式），
# icon.ico 是打包时自动转出来的产物（也可以直接放一个现成的 .ico 进来）
ICON_DIR = ROOT / "assets"
ICON_NAME = "icon"
ICON_SUFFIXES = (".png", ".ico", ".jpg", ".jpeg", ".webp")
ICON_ICO = ICON_DIR / "icon.ico"
ICON_PNG = ICON_DIR / "icon.png"          # 文档与提示里推荐的文件名
# Windows 的资源管理器会按这些尺寸取图（16 任务栏 / 32 桌面 / 48 中等图标 / 256 大图标）
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


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


def image_to_ico(source: Path, target: Path) -> Path:
    """把图标图片转成多尺寸 .ico（exe 只能嵌 .ico）。

    非正方形的图按中心裁成正方形，免得被挤扁；PNG / JPG / WEBP 都能读，
    Pillow 是程序本来就有的依赖（utils/image.py 在用），不需要额外装东西。
    """
    from PIL import Image                     # 只在打包时才需要，放函数里导入

    image = Image.open(source).convert("RGBA")
    width, height = image.size
    if width != height:
        side = min(width, height)
        left, top = (width - side) // 2, (height - side) // 2
        image = image.crop((left, top, left + side, top + side))
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    return target


def icon_source() -> Optional[Path]:
    """assets/ 下的图标源文件：icon.png 优先，其次现成的 icon.ico，再次其它图片格式。"""
    for suffix in ICON_SUFFIXES:
        candidate = ICON_DIR.joinpath(ICON_NAME + suffix)
        if candidate.is_file():
            return candidate
    return None


def make_icon() -> Optional[Path]:
    """准备打包用的 .ico；没有图标时返回 None。

    assets/ 下叫 icon 的图片就是图标：icon.png（或 jpg / webp）会转成多尺寸的
    assets/icon.ico 再用，所以换图只要替换源文件、不必手动转格式；
    直接放 assets/icon.ico 则原样使用。
    """
    source = icon_source()
    if source is None:
        print("未找到图标文件：把图标图片存成 "
              f"{ICON_PNG.relative_to(ROOT)} 后重新打包即可带上图标。")
        return None
    if source.suffix.lower() == ".ico":
        return source
    try:
        image_to_ico(source, ICON_ICO)
    except Exception as e:                # 图读不了 / 没有 Pillow：本次就不嵌图标
        print(f"图标转换失败（{e}），本次不嵌图标。")
        return None
    print(f"由 {source.name} 生成图标 {ICON_ICO.relative_to(ROOT)}")
    return ICON_ICO


def pyinstaller_command(icon: Optional[Path]) -> List[str]:
    """PyInstaller 命令行。--icon 只在 Windows 上有效，其它平台不传（传了也不会生效）。

    --windowed：双击 exe 直接开界面，不带黑框控制台（终端模式仍可用 --console，
    但需要从已有的控制台里启动）。GUI 用到的 PyQt5 / PyQtWebEngine 由 PyInstaller
    自带的 hook 处理，运行时资源（qtwebengine_resources.pak 等）也会一并打进包。
    """
    command = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm",
               "--windowed", "--name", EXE_NAME]
    if icon is not None and sys.platform == "win32":
        command += ["--icon", str(icon)]
    return command + [str(ROOT / "main.py")]


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

    # 1. 用 PyInstaller 生成单文件可执行程序（Windows 上顺带把图标嵌进 exe）
    icon = make_icon()
    if icon is not None and sys.platform != "win32":
        print("非 Windows 平台：PyInstaller 不支持 --icon，本次不嵌图标。")
    run(pyinstaller_command(icon))

    # PyInstaller 会在根目录留下 <名字>.spec 这个中间产物，不属于发布内容，顺手清掉
    spec_file = ROOT / f"{EXE_NAME}.spec"
    if spec_file.exists():
        spec_file.unlink()

    # 2. 复制可编辑资源到 dist（运行时从 exe 同目录读取；界面已全部改成 PyQt5，没有 html/ 了）
    shutil.copyfile(ROOT / "config_simple.yaml", DIST / "config.yaml")
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
