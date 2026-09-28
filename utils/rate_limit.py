"""编辑速率墙：限制向 Vocawiki 提交编辑的频率（默认每分钟 3 次，可在「设置」页改）。

为什么需要它：批量操作（修正链入页面、同步大家族模板、消歧义移动 + 封面改名…）会在几十秒里
连着改十几页，站点的 `$wgRateLimits` 会直接拒绝（错误码 `ratelimited`）；我们以前只会
「等 2 秒重试一次」，重试照样被拒。所以这里在**发请求之前**就按用户设定的频率排队。

算法：滑动窗口 —— 记住最近一分钟里每次真正提交的时刻，窗口里已经有 N 次就先睡到最早那次
滑出窗口为止（所以开头允许连着做 N 次，之后大约每 60/N 秒放行一次）。`edits_per_minute = 0`
表示不限制。

只给**写操作**排队（`wiki_api.edit_page` / `wiki_api.move_page` / `upload.upload_image`）；
查页面、搜索、取模板源码这些读操作不限速。界面里的活儿跑在 QThread 上，可能有多个线程
并发提交，所以窗口用锁保护。
"""
import logging
import threading
import time
from typing import List

WINDOW_SECONDS = 60.0            # 「一分钟 N 次」里的那个一分钟
DEFAULT_EDITS_PER_MINUTE = 3     # config.yaml 里没写 / 读不到配置时的默认值

_lock = threading.Lock()
_recent: List[float] = []        # 最近窗口内每次提交的时刻（单调时钟）
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
    """当前窗口里已经用掉的次数（日志 / 测试用）。"""
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


def wait_for_slot() -> float:
    """排队等到这一分钟里还有额度才返回；返回值是实际等了多久（秒，0 = 不用等）。"""
    limit = configured_limit()
    if limit <= 0:
        return 0.0
    waited = 0.0
    while True:
        with _lock:
            now = time.monotonic()
            _expire(now)
            if len(_recent) < limit:
                _recent.append(now)
                return waited
            wait = WINDOW_SECONDS - (now - _recent[0])
        wait = max(wait, 0.05)
        logging.info("速率墙：每分钟最多 %d 次编辑，等 %.1f 秒再提交（可在「设置」页改）",
                     limit, wait)
        waited += wait
        _sleep(wait)
