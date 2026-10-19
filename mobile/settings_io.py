"""「设置」页导入 `config.yaml` / `wiki_credentials.yaml` 的落地逻辑。

移动端选不到桌面那种「文件对话框 + 路径」，用户手里通常是自己电脑上的那两份文件
（或者从别处导出的），所以这里做三件事：**校验**内容像不像目标文件、把旧文件备份成
`.bak`、再把新内容原样写进应用私有目录。写完由调用方 `load_config()` 重新读一遍。

不导入 Kivy，方便直接跑单测。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from config import config as config_module

CONFIG_NAME = "config.yaml"
CREDENTIALS_NAME = "wiki_credentials.yaml"
BACKUP_SUFFIX = ".bak"

# 手机端第一次启动时套的默认值：生成歌曲时**先打开样式页**。
# 桌面端 `color_editor` 默认关，但桌面一直有「样式」标签页；手机上这一屏就是唯一的
# 改颜色 / 换封面入口，不开的话流程直接从歌词跳到提交（用户 2026-10-20 报）。
MOBILE_DEFAULT_VALUES = {"color.color_editor": True}

# 两种目标文件各自对应的 `parse_settings_text()` 判定结果
_KIND_BY_NAME = {CONFIG_NAME: "config", CREDENTIALS_NAME: "credentials"}


def apply_mobile_defaults(config_file: Path) -> bool:
    """第一次在手机上启动时套一遍移动端默认值（靠 `Config.mobile_defaults_applied` 只做一次）。

    之后用户在设置页怎么改就怎么算 —— 关掉「生成后调样式」也不会被再次覆盖。
    返回是否真的写了配置。
    """
    from config.config import get_config, load_config, save_config_values

    if bool(getattr(get_config(), "mobile_defaults_applied", False)):
        return False
    values = dict(MOBILE_DEFAULT_VALUES)
    values["mobile_defaults_applied"] = True
    if not save_config_values(values):
        return False
    load_config(Path(config_file))
    return True


def import_settings_bytes(raw: Optional[bytes], target: Path) -> Tuple[bool, str]:
    """同上，但吃的是**原始字节**（Android 那边 SAF 给回来的就是字节）。"""
    if not raw:
        return False, "没有读到文件内容"
    return import_settings_text(decode_settings_bytes(raw), target)


def import_settings_text(text: str, target: Path) -> Tuple[bool, str]:
    """把 `text` 当作 `target`（config.yaml / wiki_credentials.yaml）写进去。

    返回 `(成功?, 给用户看的一句话)`。会先判断内容像不像目标文件 —— 拿 `config.yaml`
    去填凭据、或者文件根本不是 YAML，都**不动**现有设置。
    """
    name = Path(target).name
    expected = _KIND_BY_NAME.get(name)
    if expected is None:
        return False, f"不支持导入到 {name}"
    if not str(text or "").strip():
        return False, "文件是空的"
    kind, _values, error = config_module.parse_settings_text(text)
    if kind != expected:
        if error:
            return False, f"导入失败：{error}"
        other = CREDENTIALS_NAME if expected == "config" else CONFIG_NAME
        return False, f"这份文件看起来是 {other}，不是 {name}"
    try:
        backup = write_with_backup(Path(target), text)
    except OSError as error:
        logging.error("写入 %s 失败：%s", target, error)
        return False, f"写入失败（{error}）"
    message = f"已导入 {name}"
    if backup is not None:
        message += f"（原文件备份为 {backup.name}）"
    return True, message


def write_with_backup(target: Path, text: str) -> Optional[Path]:
    """先备份旧文件（`config.yaml.bak`），再原子替换；返回备份路径（没有旧文件时为 None）。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Optional[Path] = None
    if target.exists():
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        backup.write_bytes(target.read_bytes())
    temp = target.with_name(target.name + ".tmp")
    # 保留用户文件里的换行（Windows 上导出的 CRLF 也照原样写回去）
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temp, target)
    return backup


def decode_settings_bytes(raw: bytes) -> str:
    """设置文件的编码识别：UTF-8（带 BOM 也认）→ 系统码页 → cp1252，最后替换模式兜底。

    与 `config._read_settings_text` 同口径：Windows 记事本存成 ANSI/GBK 的配置也要能读。
    """
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    import locale

    for encoding in (locale.getpreferredencoding(False), "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_text_file(path: Path) -> str:
    """按上面那套编码规则读一个文件（桌面调试时用得到）。"""
    return decode_settings_bytes(Path(path).read_bytes())
