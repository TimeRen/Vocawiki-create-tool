"""编辑速率墙：限制向 Vocawiki 提交编辑的频率（默认每分钟 3 次，可在「设置」页改）。

为什么需要它：批量操作（修正链入页面、同步大家族模板、消歧义移动 + 封面改名…）会在几十秒里
连着改十几页，站点的 `$wgRateLimits` 会直接拒绝（错误码 `ratelimited`）；我们以前只会
「等 2 秒重试一次」，重试照样被拒。所以这里在**发请求之前**就按用户设定的频率排队。

算法：**把「每分钟 N 次」摊平成一串平均间隔** —— 两次提交之间至少隔 `60 / N` 秒（再留半秒余量）。
用户 2026-09 定的规矩：「每分钟 3 次」= **平均每 20 秒一次**，不是「先连着改 3 次、再罚站一分钟」
（旧实现是滑动窗口，开头允许连发 N 次 —— 用户认为那正是站点判超速的原因）。
所以第一笔立刻放行，之后每一笔都排在前一笔之后 `60 / N` 秒。`edits_per_minute = 0` 表示不限制。

只给**写操作**排队（`wiki_api.edit_page` / `wiki_api.move_page` / `upload.upload_image`）；
查页面、搜索、取模板源码这些读操作不限速。界面里的活儿跑在 QThread 上，可能有多个线程
并发提交，所以窗口用锁保护。
"""
import logging
import threading
import time
from typing import List, Optional

WINDOW_SECONDS = 60.0            # 「一分钟 N 次」里的那个一分钟
DEFAULT_EDITS_PER_MINUTE = 3     # config.yaml 里没写 / 读不到配置时的默认值
MIN_GAP_PADDING = 0.5            # 间隔上多留一点余量，免得卡在临界点上被站点判超速
PADDING_RATIO = 0.1              # 间隔很短时按比例留（60 次/分钟 → 只多留 0.1 秒）

_lock = threading.Lock()
_recent: List[float] = []        # 最近一分钟内每次提交的时刻（单调时钟，只为了 recorded()）
_sleep = time.sleep              # 测试里替换掉，别真睡


def configured_limit() -> int:
    """用户设置的「每分钟最多几次编辑」（`wiki.edits_per_minute`，0 = 不限制）。

    每次都现读配置，所以在「设置」页改完保存后立刻生效，不用重启。
    """
    try:
        from config.config import get_config
        value = getattr(get_config().wiki, "edits_per_minute", DEFAULT_EDITS_PER_MINUTE)
        return max(0, int(value))
    except Exception as e:                       # noqa: BLE001 - 配置读不到就按默认值来
        logging.debug("读速率墙设置失败（按默认 %d 次/分钟）：%s", DEFAULT_EDITS_PER_MINUTE, e)
        return DEFAULT_EDITS_PER_MINUTE


def recorded() -> int:
    """最近一分钟里已经提交了几次（日志 / 测试用）。"""
    with _lock:
        _expire(time.monotonic())
        return len(_recent)


def reset() -> None:
    """清空窗口（测试用）。"""
    with _lock:
        _recent.clear()


def _expire(now: float) -> None:
    """丢掉滑出窗口的记录（调用方要持有 `_lock`）。"""
    while _recent and now - _recent[0] >= WINDOW_SECONDS:
        _recent.pop(0)


def interval_seconds(limit: Optional[int] = None) -> float:
    """两次提交之间至少要隔多久（秒）—— 把「每分钟 N 次」摊平；0 = 不限制。"""
    value = configured_limit() if limit is None else int(limit)
    if value <= 0:
        return 0.0
    base = WINDOW_SECONDS / value
    return base + min(MIN_GAP_PADDING, base * PADDING_RATIO)


def wait_for_slot() -> float:
    """排队到「离上一次提交够远」为止；返回值是实际等了多久（秒，0 = 不用等）。

    第一笔立刻放行（没有上一笔），之后每笔都等到上一笔之后 `interval_seconds()` 秒。
    """
    limit = configured_limit()
    interval = interval_seconds(limit)
    if interval <= 0:
        return 0.0
    waited = 0.0
    while True:
        with _lock:
            now = time.monotonic()
            _expire(now)
            last = _recent[-1] if _recent else None
            wait = 0.0 if last is None else interval - (now - last)
            if wait <= 0:
                _recent.append(now)
                return waited
        wait = max(wait, 0.05)
        logging.info("速率墙：每分钟最多 %d 次编辑（平均每 %.0f 秒一次），等 %.1f 秒再提交（可在「设置」页改）",
                     limit, interval, wait)
        waited += wait
        _sleep(wait)
