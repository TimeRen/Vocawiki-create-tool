#!/usr/bin/env python3
"""打包脚本：用 PyInstaller 生成单文件可执行程序，并把它与运行时资源一起打成 zip。

用法:
    python build.py            # 运行中会在终端询问版本号
    python build.py 1.0.0      # 直接指定版本号（跳过询问）
    python build.py --source 1.0.0   # 只打源码包（不跑 PyInstaller，见 `pack_source_zip`）

可执行文件的图标：把图标图片存成 assets/icon.png（正方形最好），打包时会自动转出
多尺寸的 assets/icon.ico 并用 --icon 嵌进 exe（Windows）；只放 assets/icon.ico 也可以。
两处都没有时不带图标打包，只在终端提醒一句。

完成后**只在项目根目录生成发布包** `Vocawiki-create-tool (版本号).zip`，里面是
    Vocawiki-create-tool[.exe]
    config.yaml
    wiki_credentials.yaml
    i18n/{en,zh}/LC_MESSAGES/messages.mo
解压即用（这些资源运行时从可执行文件同目录读取，必须与 exe 放在一起）。
项目里**不再生成 dist/ 目录**：exe 先放到临时目录里、打完包连同 PyInstaller 的
工作目录 build/ 一起删掉（失败时保留 build/，方便看 warn-*.txt）。
界面是 PyQt5 写的主窗口（打包成窗口程序，双击 exe 会直接打开），预览用 PyQtWebEngine。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
EXE_NAME = "Vocawiki-create-tool"
# PyInstaller 的工作目录（中间产物）；打包成功后会删掉，失败时保留供排查
WORK = ROOT / "build"
# 旧版本的脚本会在项目里留一个 dist/，打包前顺手清掉
LEGACY_DIST = ROOT / "dist"

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
    """把本地凭据文件里的密钥清空后写进发布包（保留注释与结构，避免泄露账号与 AI key）。"""
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
    """获取版本号：命令行参数优先（`--source` 这类开关不算），否则在终端询问（直接回车则用 0.0.0）。"""
    args = [arg for arg in argv[1:] if not arg.startswith("--")]
    version = args[0].strip() if args else ""
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


def staging_directory() -> Path:
    """打包时放 exe 与运行时资源的临时目录（项目里不再生成 dist/）。"""
    return Path(tempfile.mkdtemp(prefix="vocawiki-build-"))


def pack_zip(source: Path, zip_path: Path, arc_prefix: str = "") -> Path:
    """把 source 目录打成 zip（同名文件先删掉），返回 zip 路径。

    `arc_prefix` 为空时 zip 里**不套目录**（发布包就是这个样子：解压出来就是 exe 与
    config.yaml / i18n/ 等资源）；源码包传一个目录名，解压后自然多一层。
    """
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                name: Path = file.relative_to(source)
                if arc_prefix:
                    name = Path(arc_prefix) / name
                zf.write(file, name)
    return zip_path


# 源码包里带的东西（不含 .venv / build / output / 测试与打包脚本）
SOURCE_ITEMS = ("config", "i18n", "models", "utils", "main.py", "parse_lyrics.py",
                "process_image.py", "README.md", "config_simple.yaml",
                "wiki_credentials.yaml", "requirements.txt")


def pack_source_zip(version: str) -> Path:
    """打**源码包**（`python build.py --source [版本号]`，`make_source.sh` / `make source` 走的就是它）。

    和 exe 发布包一样不生成 `dist/`：先把源码复制到临时目录（顶层目录名就是包名），
    把 `config_simple.yaml` 改名成 `config.yaml`、现编译 .mo，再压缩、删临时目录。
    """
    folder_name = f"{EXE_NAME} ({version})"
    staging = staging_directory()
    try:
        staging.mkdir(parents=True, exist_ok=True)
        for item in SOURCE_ITEMS:
            source = ROOT / item
            if item == "wiki_credentials.yaml":
                # 不拷真文件：密钥必须清空（旧 make_source.sh 直接把真凭据拷了进去）
                write_credentials_template(staging / item)
            elif not source.exists():
                print(f"源码包里没有 {item}，跳过。")
            elif source.is_dir():
                shutil.copytree(source, staging / item,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(source, staging / item)
        packaged_config = staging / "config_simple.yaml"
        if packaged_config.exists():
            packaged_config.rename(staging / "config.yaml")
        else:
            print("源码包里没有 config_simple.yaml，跳过改名。")
        run([sys.executable, str(ROOT / "compile_mo.py"), str(staging / "i18n")])
        # 顶层目录名靠 arc_prefix，不靠多复制一层目录（否则 zip 里会套两层）
        return pack_zip(staging, ROOT / zip_name_for(version), arc_prefix=folder_name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def pyinstaller_command(icon: Optional[Path], dist_path: Path) -> List[str]:
    """PyInstaller 命令行；exe 输出到 dist_path（打包用的临时目录，不是项目里的 dist/）。

    --windowed：双击 exe 直接开界面，不带黑框控制台（终端模式仍可用 --console，
    但需要从已有的控制台里启动）。GUI 用到的 PyQt5 / PyQtWebEngine 由 PyInstaller
    自带的 hook 处理，运行时资源（qtwebengine_resources.pak 等）也会一并打进包。
    图标既用 --icon 嵌进 exe（资源管理器 / 任务栏看到的那张），也用 --add-data
    放进包内 assets/（窗口标题栏从 sys._MEIPASS 找得到，见 utils/ui/window._app_icon）。
    """
    command = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm",
               "--windowed", "--name", EXE_NAME,
               "--distpath", str(dist_path), "--workpath", str(WORK)]
    if icon is not None:
        command += ["--add-data", f"{icon}{os.pathsep}assets"]
        if sys.platform == "win32":
            command += ["--icon", str(icon)]
    return command + [str(ROOT / "main.py")]


def main():
    # 统一在项目根目录下构建，避免受调用目录影响
    os.chdir(ROOT)

    version = ask_version(sys.argv)                   # 先问版本号，再开始耗时的打包
    if "--source" in sys.argv[1:]:                    # 只打源码包（不跑 PyInstaller）
        print("源码包完成：", pack_source_zip(version))
        return

    # 清理旧的中间产物（含旧版脚本留下的 dist/）
    for path in (WORK, LEGACY_DIST, ROOT / f"{EXE_NAME}.spec"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    staging = staging_directory()
    succeeded = False
    try:
        # 1. 用 PyInstaller 生成单文件可执行程序（Windows 上顺带把图标嵌进 exe）
        icon = make_icon()
        if icon is not None and sys.platform != "win32":
            print("非 Windows 平台：PyInstaller 不支持 --icon，本次不嵌图标。")
        run(pyinstaller_command(icon, staging))

        # 非 Windows 上确认可执行位（旧 Makefile 用 chmod 544 保证这一点）
        exe = staging / (EXE_NAME + ".exe" if os.name == "nt" else EXE_NAME)
        if exe.exists() and os.name != "nt":
            exe.chmod(0o755)

        # PyInstaller 会在根目录留下 <名字>.spec 这个中间产物，不属于发布内容，顺手清掉
        spec_file = ROOT / f"{EXE_NAME}.spec"
        if spec_file.exists():
            spec_file.unlink()

        # 2. 把可编辑资源放到 exe 旁边（运行时从 exe 同目录读取；界面已全部改成 PyQt5，没有 html/）
        shutil.copyfile(ROOT / "config_simple.yaml", staging / "config.yaml")
        write_credentials_template(staging / "wiki_credentials.yaml")
        shutil.copytree(ROOT / "i18n", staging / "i18n",
                        ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.pyc"))

        # 3. 编译 .po -> .mo
        run([sys.executable, str(ROOT / "compile_mo.py"), str(staging / "i18n")])

        # 4. 打成 zip：Vocawiki-create-tool (版本号).zip
        zip_path = pack_zip(staging, ROOT / zip_name_for(version))
        succeeded = True
        print("打包完成：", zip_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)     # 临时目录（exe 与资源）不留在项目里
        if succeeded:
            shutil.rmtree(WORK, ignore_errors=True)    # 成功时中间产物也一并清掉
        else:
            print(f"打包未完成，保留 {WORK.name}/ 供排查："
                  f"{WORK / EXE_NAME / ('warn-' + EXE_NAME + '.txt')}")


if __name__ == "__main__":
    main()
