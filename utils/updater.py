"""从 GitHub Releases 检查 / 下载新版本（exe 的 zip 与 Android 的 apk）。

为什么要有它：以前每改一次代码，用户都得自己重新跑一遍 `build.bat`（打 exe）或等 APK
重新打包，再手动把文件换掉。这里把「问一下 GitHub 有没有新构建 → 下载 → 装上去」做进
设置页，exe 与 APK 共用同一套（用户 2026-10-20 要求）。

发布端的约定（见 `.github/workflows/android-apk.yml`）：CI 只维护**一个**滚动 release
（tag 固定 `latest`），每次推送把里面的 `Vocawiki-create-tool-latest.apk` /
`…-latest.zip` 覆盖掉（不按推送建一堆 release）；正文里写着 `version:` / `commit:`，
这里读的就是「最新那个 release」，按扩展名挑出要下的文件。
"""
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from utils.helpers import http_get

try:                                                             # 打包时由 build.py 生成
    import _build_info                                          # noqa: F401
except Exception:                                               # noqa: BLE001 - 源码运行没有它
    _build_info = None

REPO = "TimeRen/Vocawiki-create-tool"
# CI 维护的那个滚动 release（tag 固定 `latest`，标成 prerelease，免得抢走正式版的 "Latest" 标记）。
# 用 `/releases/tags/<tag>` 取它：prerelease 不在 `/releases/latest` 里。
RELEASE_TAG = "latest"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE_TAG}"
# 没有那个 tag 时退回「GitHub 认为最新的正式版」
FALLBACK_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
# 说明里写的版本号（`version: 1.0.27`）优先，没有就拿 tag 当版本号
VERSION_LINE_RE = re.compile(r"^\s*version\s*[:=]\s*([0-9A-Za-z._-]+)\s*$", re.MULTILINE)
APK_SUFFIX = ".apk"
EXE_SUFFIX = ".zip"
DOWNLOAD_CHUNK = 256 * 1024
CHECK_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300
# 更新包下到**固定的**目录里：断点续传要能跨「点两次按钮」甚至跨一次重启找到半截文件
# （用户 2026-10-24 要求；以前每次 `mkdtemp()`，断了就得从 0 开始）。
DOWNLOAD_DIR_NAME = "vocawiki-update"


def download_dir() -> Path:
    """更新包的下载目录（`%TEMP%/vocawiki-update`，固定位置，方便续传）。"""
    folder = Path(tempfile.gettempdir()).joinpath(DOWNLOAD_DIR_NAME)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def download_target(name: str) -> Path:
    """按发布包的文件名给出下载目标（`<临时目录>/vocawiki-update/<name>`）。"""
    return download_dir().joinpath(Path(str(name or "")).name or "update.bin")


class UpdaterError(RuntimeError):
    """检查更新失败（网络 / 没有发过 release / 文件不对）—— 消息直接给用户看。"""


@dataclass
class ReleaseInfo:
    """GitHub 上最新那个 release 里我们关心的东西。"""

    version: str = ""
    tag: str = ""
    published_at: str = ""
    page_url: str = RELEASES_PAGE
    notes: str = ""
    assets: Dict[str, str] = field(default_factory=dict)      # 文件名 → 下载地址

    def asset(self, suffix: str) -> Tuple[str, str]:
        """按扩展名挑一个文件 → `(文件名, 下载地址)`；没有就是 `("", "")`。"""
        for name, url in self.assets.items():
            if name.lower().endswith(suffix):
                return name, url
        return "", ""

    def for_platform(self, kind: str = "") -> Tuple[str, str]:
        """本平台该下哪一个：Android 要 APK，Windows 要 zip。"""
        kind = kind or platform_kind()
        return self.asset(APK_SUFFIX if kind == "android" else EXE_SUFFIX)


def platform_kind() -> str:
    """`"android"` / `"windows"` / `"macos"` / `"linux"`。"""
    if "ANDROID_ARGUMENT" in os.environ or sys.platform.startswith("android"):
        return "android"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def build_info() -> Dict[str, str]:
    """当前这份程序是哪个版本（打包时写进 `_build_info.py`，见该文件说明）。"""
    module = _build_info
    if module is None:
        return {"version": "", "built_at": "", "commit": ""}
    return {
        "version": str(getattr(module, "VERSION", "") or ""),
        "built_at": str(getattr(module, "BUILT_AT", "") or ""),
        "commit": str(getattr(module, "COMMIT", "") or ""),
    }


def version_key(text: str) -> Optional[Tuple[int, ...]]:
    """版本号 → 可比较的数字元组（`1.0.27` → `(1, 0, 27)`）；认不出返回 None。"""
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(number) for number in numbers) if numbers else None


def is_newer(latest: str, current: str) -> bool:
    """`latest` 是不是比 `current` 新。任一边认不出数字时按「有新版」处理。"""
    left, right = version_key(latest), version_key(current)
    if left is None or right is None:
        return bool(str(latest or "").strip())
    return left > right


def fetch_latest(use_proxy: bool = True) -> ReleaseInfo:
    """问 GitHub「滚动构建」是哪一版（没有 release / 网络不通都抛 `UpdaterError`）。"""
    response = _get(LATEST_API, use_proxy)
    if response.status_code == 404:                 # 还没发过滚动构建 → 退回正式版
        response = _get(FALLBACK_API, use_proxy)
    if response.status_code == 404:
        raise UpdaterError("这个仓库还没有发布过版本（GitHub Releases 是空的）")
    if response.status_code != 200:
        raise UpdaterError(f"GitHub 返回 {response.status_code}（可能是代理没开或请求太频繁）")
    payload = _payload(response)
    release = ReleaseInfo(
        tag=str(payload.get("tag_name") or ""),
        published_at=str(payload.get("published_at") or payload.get("created_at") or ""),
        page_url=str(payload.get("html_url") or RELEASES_PAGE),
        notes=str(payload.get("body") or ""),
    )
    match = VERSION_LINE_RE.search(release.notes)
    release.version = (match.group(1) if match else release.tag).strip()
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name and url:
            release.assets[name] = url
    return release


def _get(url: str, use_proxy: bool):
    try:
        return http_get(url, use_proxy=use_proxy, timeout=CHECK_TIMEOUT,
                        headers={"Accept": "application/vnd.github+json"})
    except Exception as e:                                       # noqa: BLE001 - 统一给用户看
        raise UpdaterError(f"连不上 GitHub：{e}") from e


def _payload(response) -> Dict[str, object]:
    try:
        return response.json()
    except Exception as e:                                       # noqa: BLE001
        raise UpdaterError(f"GitHub 的回复看不懂：{e}") from e


def summarise(latest: ReleaseInfo, current: Optional[Dict[str, str]] = None) -> str:
    """一句话说明「现在是不是最新」——设置页里直接显示这句。"""
    current = current if current is not None else build_info()
    here = current.get("version") or "未知"
    if is_newer(latest.version, current.get("version", "")):
        return f"发现新版本 {latest.version}（当前 {here}）"
    return f"已是最新（当前 {here}，最新 {latest.version}）"


def download(url: str, dest: Path, progress: Optional[Callable[[int, int], None]] = None,
             use_proxy: bool = True, resume: bool = True) -> Path:
    """把文件下到 `dest`；`progress(已下载, 总字节)` 由调用方决定怎么显示。

    **断点续传**（用户 2026-10-24 要求）：先下到 `dest.part`，网络抖了就停在那儿，
    下次用 `Range` 从断点接着下（GitHub 的下载链接支持 Range）。以前出错就把半截文件删掉，
    一个 140 MB 的更新包断一次就得从头再来一遍。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(dest.suffix + ".part")
    done = temp.stat().st_size if (resume and temp.exists()) else 0
    if temp.exists() and not done:
        temp.unlink(missing_ok=True)
    headers = {"Range": f"bytes={done}-"} if done else None
    if done:
        logging.info("接着上次的进度继续下载 %s（已有 %.1f MB）", dest.name, done / (1024 * 1024))
    try:
        response = http_get(url, use_proxy=use_proxy, timeout=DOWNLOAD_TIMEOUT, stream=True,
                            headers=headers)
    except Exception as e:                                       # noqa: BLE001
        raise UpdaterError(f"下载失败：{e}") from e
    if done and response.status_code == 416:
        # 「Range 不成立」：半截文件比文件本身还大（上次其实是完整的 / 远端换了文件）→ 重来
        temp.unlink(missing_ok=True)
        logging.info("断点续传被拒绝（416），从头下载 %s", dest.name)
        return download(url, dest, progress, use_proxy, resume=False)
    if done and response.status_code == 200:
        logging.info("服务器没理会断点续传，从头下载 %s", dest.name)
        done = 0
        temp.unlink(missing_ok=True)
    if response.status_code not in (200, 206):
        temp.unlink(missing_ok=True)
        raise UpdaterError(f"下载失败：GitHub 返回 {response.status_code}")
    if response.status_code == 206:
        # 续传时 `Content-Length` 只是「剩下多少」，总量在 `Content-Range` 里
        match = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range") or "")
        total = int(match.group(1)) if match else 0
    else:
        total = int(response.headers.get("Content-Length") or 0)
    if progress is not None and done:
        progress(done, total)                    # 先把续传的起点报给界面
    mode = "ab" if (done and response.status_code == 206) else "wb"
    written = done
    try:
        with open(temp, mode) as handle:
            for chunk in response.iter_content(DOWNLOAD_CHUNK):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total)
    except Exception as e:                                       # noqa: BLE001 - 网络抖动
        logging.warning("下载 %s 中断（已下 %d 字节，留着下次续传）：%s", dest.name, written, e)
        raise UpdaterError(f"下载中断（已下 {written} 字节，下次会接着下）：{e}") from e
    if total and written < total:
        # 半截文件留着：下次从这儿接着下
        raise UpdaterError(f"下载不完整（{written}/{total} 字节），下次会接着下")
    os.replace(temp, dest)
    return dest


# ---------------------------------------------------------------- 装上去：Windows

DESKTOP_EXE = "Vocawiki-create-tool.exe"
# 只换程序本体：`config.yaml` / `wiki_credentials.yaml` 是用户自己的设置，绝不能盖掉
KEEP_NAMES = ("config.yaml", "wiki_credentials.yaml")
SWAP_SCRIPT = """@echo off
chcp 65001 >nul
rem 由设置页「检查更新」写出：等旧进程退出 → 覆盖 exe → 重新启动 → 清掉临时文件
rem 启动新进程前必须重置 PyInstaller 的运行环境（见 `_relaunch_env()`）
set "PYINSTALLER_RESET_ENVIRONMENT=1"
:wait
copy /y "{new_exe}" "{target}" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
start "" "{target}"
timeout /t 2 /nobreak >nul
rmdir /s /q "{work}" >nul 2>&1
del "%~f0" >nul 2>&1
"""

# PyInstaller 6 会把 `_PYI_APPLICATION_HOME_DIR` / `_PYI_ARCHIVE_FILE` 这类内部变量传给它
# 启动的子进程，子进程于是**复用父进程的 `_MEI…` 解压目录**。更新时我们正是从正在运行的
# exe 里再启动一次自己：父进程退出时删不掉那个目录（弹出的
# 「Failed to remove temporary directory: …\_MEI…」就是它），子进程还继续从被删了一半的
# 目录里读 certifi / DLL —— HTTPS 请求（登录 Vocawiki 等）当场失败，必须再重启一次才好。
# 官方给「自己重启自己」的开关就是这个环境变量：置 1 后 bootloader 会重新解压到新目录。
RELAUNCH_ENV = "PYINSTALLER_RESET_ENVIRONMENT"


def _relaunch_env() -> Dict[str, str]:
    """重启用的环境：在现有环境上让 PyInstaller 重新解压（见 `RELAUNCH_ENV`）。"""
    env = dict(os.environ)
    env[RELAUNCH_ENV] = "1"
    return env


def exe_path() -> Optional[Path]:
    """当前跑着的 exe（源码运行时没有）。"""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def install_desktop(archive: Path) -> Tuple[bool, str]:
    """解压新包、替换 exe 并重启（不动用户的配置文件），返回 (是否成功, 说明)。"""
    target = exe_path()
    if target is None:
        return False, "当前是从源码运行的，没有可替换的 exe —— 请到发布页下载新版本"
    if not archive.exists():
        return False, "下载的文件不见了，请重新检查更新"
    work = Path(tempfile.mkdtemp(prefix="vocawiki-update-"))
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(work)
        new_exe = next((path for path in work.rglob(DESKTOP_EXE) if path.is_file()), None)
        if new_exe is None:
            return False, "下载的包里没有 exe，可能下错了文件"
        _copy_sidecar(new_exe.parent, target.parent)
        if _swap_now(new_exe, target):
            _restart(target)
            return True, "已更新，正在重新启动"
        script = _write_swap_script(new_exe, target, work)
        if script is None:
            return False, "替换 exe 失败（文件被占用？）请关掉程序后重试"
        subprocess.Popen(["cmd", "/c", str(script)], close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True, "正在关闭并替换程序，几秒后会自动重新打开"
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"解压更新包失败：{e}"
    finally:
        archive.unlink(missing_ok=True)


def _copy_sidecar(source: Path, target_dir: Path) -> None:
    """顺带更新 `i18n/`（翻译）；`config.yaml` / `wiki_credentials.yaml` 一律不碰。"""
    if any((source / name).exists() for name in KEEP_NAMES):
        logging.debug("更新包里带了配置文件，跳过（不能覆盖用户设置）")
    source_i18n = source / "i18n"
    if not source_i18n.is_dir():
        return
    try:
        shutil.copytree(source_i18n, target_dir / "i18n", dirs_exist_ok=True)
    except OSError as e:
        logging.warning("更新 i18n 失败（不影响使用）：%s", e)


def _swap_now(new_exe: Path, target: Path) -> bool:
    """当场把 exe 换掉：运行中的 exe 允许**改名**，于是「改名 → 拷新的 → 删旧的」。"""
    old = target.with_suffix(target.suffix + ".old")
    try:
        old.unlink(missing_ok=True)
        os.replace(target, old)                      # 运行中也能改名（Windows 允许）
        shutil.copy2(new_exe, target)
        return True
    except OSError as e:
        logging.warning("当场替换 exe 失败，改用脚本等它退出：%s", e)
        if not target.exists() and old.exists():     # 改完名却没拷成功 → 先还原
            try:
                os.replace(old, target)
            except OSError:
                pass
        return False


def _restart(target: Path) -> None:
    """启动新版本的 exe。

    `env` 里必须带 `PYINSTALLER_RESET_ENVIRONMENT=1`（见 `RELAUNCH_ENV`）：不加的话新进程会
    复用旧进程的 `_MEI…` 目录，父进程退出时删不掉（弹「Failed to remove temporary directory」），
    新进程还会因为目录被删而登录失败 —— 用户 2026-10-21 报的就是这个。
    """
    try:
        subprocess.Popen([str(target)], cwd=str(target.parent), close_fds=True,
                         env=_relaunch_env())
    except OSError as e:
        logging.warning("重启失败，请手动打开程序：%s", e)


def cleanup_leftovers() -> None:
    """删掉上次更新换下来的 `…exe.old`（运行时那个文件还被占着，只能等下次启动）。"""
    target = exe_path()
    if target is None:
        return
    old = target.with_suffix(target.suffix + ".old")
    try:
        if old.exists():
            old.unlink()
            logging.info("已清理上次更新留下的 %s", old.name)
    except OSError as e:
        logging.debug("清理旧 exe 失败（下次启动再试）：%s", e)


def _write_swap_script(new_exe: Path, target: Path, work: Path) -> Optional[Path]:
    """把「等退出 → 覆盖 → 重启」的批处理写到 `work` 外面（它要删掉 `work`）。"""
    folder = Path(tempfile.mkdtemp(prefix="vocawiki-update-script-"))
    script = folder / "apply-update.bat"
    try:
        script.write_text(SWAP_SCRIPT.format(new_exe=new_exe, target=target, work=work),
                          encoding="utf-8")
    except OSError as e:
        logging.warning("写更新脚本失败：%s", e)
        return None
    return script


# ---------------------------------------------------------------- 装上去：Android

def install_android(apk: Path) -> Tuple[bool, str]:
    """把 APK 交给系统的安装界面（走 `mobile.platform.install_apk()`）。

    首次安装会被系统拦下来问「允许从此来源安装应用」——那一步只能用户自己点。
    """
    path = Path(apk)
    if not path.is_file():
        return False, "更新包不见了，请重新下载"
    if platform_kind() != "android":
        return False, f"桌面版请解压覆盖 exe：{path}"
    try:
        from mobile import platform as mobile_platform      # 懒导入：桌面上没有 jnius / kivy
    except Exception as e:                                   # noqa: BLE001
        logging.warning("装 APK 需要 Android 运行时：%s", e)
        return False, "当前环境不能直接安装，请到发布页下载"
    return mobile_platform.install_apk(path)
