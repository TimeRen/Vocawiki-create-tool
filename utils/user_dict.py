"""用户手填过的名字（译名 / 歌姬）记在本地，下次直接沿用，不用重填。

场景（用户 2026-11-15 要求）：在「榜单」页里把某个日文曲名手填了中文译名、或把
`和音マコ` 手改成 `和音Mako`，下一次同一首歌再出现时应该自动沿用。

文件放在 `config.yaml` 旁边（`user_names.yaml`），写不进去（比如装在了 Program Files）
就退回输出目录：

    # 用户手填的名字（程序优先用它，不再重复搜 / 重复问）
    vocalists:
      和音マコ: 和音Mako
    titles:
      ワールドイズマイン: 世界属于我

只记**用户真改过的**（调用方负责比较「自动算出来的值」与「最终值」），所以这个文件
不会因为程序升级而变得不可信。删掉它就等于「忘掉所有手填」。
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

import yaml

FILE_NAME = "user_names.yaml"
SECTIONS = ("vocalists", "titles")

_cache: Optional[Dict[str, Dict[str, str]]] = None
_path_override: Optional[Path] = None


def _candidate_paths():
    """先放程序目录（与 config.yaml 同处），写不进去再退到输出目录。"""
    paths = []
    try:
        from config.config import application_path, get_output_path

        paths.append(Path(application_path) / FILE_NAME)
        paths.append(Path(get_output_path()) / FILE_NAME)
    except Exception:                                # 单测里可能还没配好 config
        pass
    paths.append(Path(FILE_NAME))
    return paths


def path() -> Path:
    """词典文件的位置（默认程序目录；显式 `set_path()` 过就用那个）。"""
    if _path_override is not None:
        return _path_override
    for candidate in _candidate_paths():
        if candidate.exists():
            return candidate
    candidates = _candidate_paths()
    return candidates[0] if candidates else Path(FILE_NAME)


def set_path(value) -> None:
    """指定词典文件（测试与「换个地方存」用）；传 None 恢复默认。"""
    global _path_override, _cache
    _path_override = Path(value) if value else None
    _cache = None


def _empty() -> Dict[str, Dict[str, str]]:
    return {section: {} for section in SECTIONS}


def load(force: bool = False) -> Dict[str, Dict[str, str]]:
    """读词典（读一次就缓存在内存；文件坏了当空表）。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    data = _empty()
    target = path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        _cache = data
        return data
    try:
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        logging.warning("用户词典解析失败（当作空表）：%s", error)
        _cache = data
        return data
    if isinstance(loaded, dict):
        for section in SECTIONS:
            table = loaded.get(section)
            if isinstance(table, dict):
                data[section] = {str(key): str(value) for key, value in table.items()
                                 if str(key).strip() and str(value).strip()}
    _cache = data
    return data


def save() -> bool:
    """把内存里的词典写回文件（原子写；失败只记日志，不影响生成流程）。"""
    data = load()
    target = path()
    lines = ["# 用户手填的名字（程序优先用它，不再重复搜 / 重复问）",
             "# 删掉这个文件＝忘掉所有手填；手工改也可以，键是日文原名"]
    for section in SECTIONS:
        table = data.get(section) or {}
        lines.append(f"{section}:")
        if not table:
            lines[-1] = f"{section}: {{}}"
            continue
        for key in sorted(table):
            lines.append(f"  {_quote(key)}: {_quote(table[key])}")
    text = "\n".join(lines) + "\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="user_names_", suffix=".yaml",
                                        dir=str(target.parent))
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp, target)
    except OSError as error:
        logging.warning("写用户词典失败（%s）：%s", target, error)
        _remove_leftover(target)
        return False
    return True


def _remove_leftover(target: Path) -> None:
    for file in target.parent.glob("user_names_*.yaml"):
        try:
            file.unlink()
        except OSError:
            pass


def _quote(value: str) -> str:
    """值里出现冒号 / 井号这类 YAML 特别字符时加引号。"""
    text = str(value)
    if text == "" or any(char in text for char in ":#{}[],&*!|>%@`\"'") or text != text.strip():
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def lookup_vocalist(name: str) -> str:
    """查「日文歌姬名 → 站上写法」；没有就返回空串。"""
    key = str(name or "").strip()
    return load()["vocalists"].get(key, "") if key else ""


def lookup_title(title: str) -> str:
    """查「日文曲名 → 条目名（译名）」；没有就返回空串。"""
    key = str(title or "").strip()
    return load()["titles"].get(key, "") if key else ""


def remember_vocalist(japanese: str, chinese: str) -> bool:
    """记住一组歌姬名（两侧都非空、且不同才记）；返回是否真的记了。"""
    return _remember("vocalists", japanese, chinese)


def remember_title(japanese: str, entry: str) -> bool:
    """记住一组「日文曲名 → 条目名」；返回是否真的记了。"""
    return _remember("titles", japanese, entry)


def _remember(section: str, japanese: str, value: str) -> bool:
    japanese = str(japanese or "").strip()
    value = str(value or "").strip()
    if not japanese or not value or japanese == value:
        return False
    table = load()[section]
    if table.get(japanese) == value:
        return False
    table[japanese] = value
    return save()


def clear() -> None:
    """忘掉所有手填（文件也删掉）。"""
    global _cache
    _cache = _empty()
    try:
        path().unlink()
    except OSError:
        pass


__all__ = ["path", "set_path", "load", "save", "lookup_vocalist", "lookup_title",
           "remember_vocalist", "remember_title", "clear", "FILE_NAME"]
