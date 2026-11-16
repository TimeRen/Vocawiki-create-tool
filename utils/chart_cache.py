"""同一期榜单的识别结果缓存：「沿用上次结果」就不用再下载、再识别一遍。

用户 2026-11-15 的截图里，一次「搜完一遍又识别一遍」把 53 MB 视频重新下载、
373 帧重新抽取、28 批 AI 重新跑 —— 纯属浪费。所以识别完就把结果存在
`输出目录/.chart_cache/<榜单>-<期数>.json`，下次进来先问一句：

    第53期上次识别过（45行，2小时前）。沿用上次结果，还是重新下载并识别？

* 「沿用」→ 直接读这份 JSON，**一次网络请求都不发**；
* 「重新识别」→ 整段重做并覆盖（`created_at` 于是变成这次的时间）；
* 删掉 `.chart_cache` 目录＝强制全部重新识别。

这里只负责「读写 JSON」，行的结构由 `utils/chart_editor.py` 负责（它有自己的
`ChartRow.to_dict()` / `from_dict()`），所以本模块不认识 `ChartRow`，也不会造成循环导入。
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.chart_nico import ChartSpec

FOLDER_NAME = ".chart_cache"
# 缓存格式版本：识别口径一改（角标/小节/只收录前 20 名）就得让旧缓存失效，
# 否则用户点「沿用上次结果」会继续看到老毛病（2026-11-16 的 109 行、零角标那份）。
CACHE_VERSION = 2
# 一份缓存里的固定字段（多出来的键照样保留，只是这几个必须有）
WORK_FIELDS = ("video_id", "uploader", "publish", "start", "end", "rule", "description",
               "bilibili", "notes", "frames", "source")

_folder_override: Optional[Path] = None


def set_folder(value) -> None:
    """指定缓存目录（测试与「换个地方存」用）；传 None 恢复默认。"""
    global _folder_override
    _folder_override = Path(value) if value else None


def folder() -> Path:
    """缓存目录（放输出目录下，跟着用户的输出路径走）。"""
    if _folder_override is not None:
        return _folder_override
    from config.config import get_output_path

    return Path(get_output_path()) / FOLDER_NAME


def path_for(spec: ChartSpec, index: int) -> Path:
    """`U榜第53期` → `<输出目录>/.chart_cache/utau-53.json`。"""
    return folder() / f"{spec.key}-{int(index)}.json"


def load(spec: ChartSpec, index: int) -> Optional[dict]:
    """读缓存（没有 / 坏了 → None，不抛异常）。"""
    target = path_for(spec, index)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except ValueError as error:
        logging.warning("榜单缓存解析失败（当作没有）：%s", error)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def payload_for(spec: ChartSpec, index: int, work) -> dict:
    """把 `ChartWork` 摊平成可写的 JSON（行用 `ChartRow.to_dict()`）。"""
    payload = {"version": CACHE_VERSION, "chart": spec.key, "index": int(index),
               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for name in WORK_FIELDS:
        payload[name] = getattr(work, name, "")
    payload["rows"] = [row.to_dict() if hasattr(row, "to_dict") else dict(row)
                       for row in (getattr(work, "rows", None) or [])]
    return payload


def save(spec: ChartSpec, index: int, work) -> bool:
    """写缓存（原子写；失败只记日志 —— 存不下也不该影响生成流程）。"""
    target = path_for(spec, index)
    text = json.dumps(payload_for(spec, index, work), ensure_ascii=False, indent=1)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="chart_", suffix=".json", dir=str(target.parent))
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
        os.replace(temp, target)
    except OSError as error:
        logging.warning("写榜单缓存失败（%s）：%s", target, error)
        return False
    return True


def row_count(payload: Optional[dict]) -> int:
    """缓存里有几行数据。"""
    rows = (payload or {}).get("rows")
    return len(rows) if isinstance(rows, list) else 0


def is_usable(payload: Optional[dict]) -> bool:
    """有行数据、且是当前版本的缓存，才值得问「要不要沿用」。

    旧版本的缓存（例如 2026-11-16 那份 109 行、一个角标都没有的）直接当没有 ——
    否则用户点「沿用上次结果」会以为新版本还有老毛病。
    """
    if row_count(payload) <= 0:
        return False
    return int((payload or {}).get("version") or 0) == CACHE_VERSION


def age_text(created_at: object) -> str:
    """`created_at` → `2小时前` / `刚刚` / 空串（认不出时间就返回空串）。"""
    moment = _parse_time(created_at)
    if moment is None:
        return ""
    delta = datetime.now(timezone.utc) - moment
    if delta < timedelta(0):
        delta = timedelta(0)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    return f"{delta.days}天前"


def _parse_time(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def reuse_hint(spec: ChartSpec, index: int, payload: Optional[dict]) -> str:
    """`第53期上次识别过（45行，2小时前）` 这样一句给人看的话。"""
    age = age_text((payload or {}).get("created_at"))
    rows = row_count(payload)
    text = f"第{index}期上次识别过（{rows}行"
    return f"{text}，{age}）" if age else f"{text}）"


def clear(spec: Optional[ChartSpec] = None, index: Optional[int] = None) -> int:
    """删缓存：给了 `spec`/`index` 只删那一期，否则清空整个目录。返回删掉的文件数。"""
    if spec is not None and index is not None:
        targets = [path_for(spec, index)]
    else:
        try:
            targets = list(folder().glob("*.json"))
        except OSError:
            targets = []
    removed = 0
    for target in targets:
        try:
            target.unlink()
            removed += 1
        except OSError:
            continue
    return removed


__all__ = ["folder", "set_folder", "path_for", "load", "save", "payload_for", "row_count",
           "is_usable", "age_text", "reuse_hint", "clear", "FOLDER_NAME", "WORK_FIELDS",
           "CACHE_VERSION"]
