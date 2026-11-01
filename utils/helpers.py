import logging
import math
import socket
import time
from typing import Callable, List, Optional, Union

import requests

from config.config import get_config
from utils import identity, ui
from utils.save_input import save_input
from utils.string import is_empty

# 「代理 / 加速器」的 fake-IP 段（Clash、Surge 这类 TUN 模式默认拿它们当假地址）。
# 命中基本可以断定：域名被本地代理接管了，而那条路径此刻不通。
FAKE_IP_RANGES = (("198.18.0.0", "198.19.255.255"),)
# 网络类错误重试的间隔（秒），`http_get(retries=n)` 时用；越往后等越久
RETRY_BACKOFF = (1.0, 3.0)
# 这些异常多半是「等一下就好」的（TLS 握手被掐断、连接被重置、超时）
RETRYABLE_ERRORS = (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)


def get_input() -> str:
    s = input()
    save_input(s)
    return s


def prompt_response(prompt: str, auto_strip: bool = True,
                    validity_checker: Callable[[str], bool] = lambda x: True) -> str:
    # GUI 已经启动时（main.py 走界面）在这里转向主窗口提问，其余调用点不用改
    if ui.is_active():
        answer = ui.ask_response(prompt, auto_strip=auto_strip,
                                 validity_checker=validity_checker)
        save_input(answer)
        return answer
    print(prompt)
    while True:
        s = get_input()
        if auto_strip:
            s = s.strip()
        if validity_checker(s):
            return s


def get_number_validity_checker(start: int, end: int) -> Callable[[str], bool]:
    def validity_checker(response: str):
        try:
            r = int(response)
            if start <= r <= end:
                return True
            else:
                print(f"{r} is not in range.")
        except Exception as e:
            print(e)
            return False

    return validity_checker


def prompt_choices(prompt: str, choices: List[str], allow_zero: bool = False) -> int:
    if ui.is_active():                        # 界面上直接点按钮，不用输编号
        answer = ui.ask_choices(prompt, choices, allow_zero=allow_zero)
        save_input(str(answer))
        return answer
    prompt += "\n" + "\n".join([f"{index + 1}: {choice}"
                                for index, choice in enumerate(choices)])
    min_val = 0 if allow_zero else 1
    return int(prompt_response(prompt, validity_checker=get_number_validity_checker(min_val, len(choices))))


def prompt_number(prompt: str, start: int = -math.inf, end: int = math.inf) -> int:
    return int(prompt_response(prompt, validity_checker=get_number_validity_checker(start, end)))


def prompt_multiline(prompt: str, terminator: Union[Callable[[str], bool], str] = is_empty,
                     auto_strip: bool = True) -> List[str]:
    if ui.is_active():                        # 界面里整段粘进去，点「完成」即可
        lines = ui.ask_multiline(prompt, auto_strip=auto_strip, terminator=terminator)
        for line in lines:
            save_input(line)
        save_input("")
        return lines
    if isinstance(terminator, str):
        string = terminator

        def t(x: str): return x == string

        terminator = t
    print(prompt)
    res = []
    while True:
        s = get_input()
        if terminator(s):
            return res
        if auto_strip:
            s = s.strip()
        res.append(s)


def http_get(url: str, use_proxy: bool, retries: int = 0, **kwargs):
    """GET 一个**站外**地址（niconico / YouTube / bilibili / vocadb / 网易云 …）。

    UA 规则（用户 2026-09 要求）：**站外一律用普通浏览器 UA**。工具自己的 UA 只发给
    用户配置的那个 wiki（见 `utils/login.py` 的 `WikiSession`）——别人的站点不需要知道
    是哪个程序在抓数据，也就省掉「先被挡一次、再换 UA 重试」那次多余请求。
    调用方**显式**传了 `User-Agent` 的就以它为准（本函数不再插手）。

    `retries`：网络类异常（TLS 握手被掐断 / 连接重置 / 超时）再试几次，默认 0（不改变
    其它调用方的现有行为）。每次重试都是一次**全新连接**（不复用上次那个 socket）——
    Cloudflare 那类握手失败常常只是那一次被掐，重试就能过。

    注意：发往 Vocawiki 的请求不走这里（走 `WikiSession`，那边只用工具 UA、不降级）。
    见 `utils/identity.py` 的说明。
    """
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", identity.BROWSER_USER_AGENT)
    attempt = 0
    while True:
        try:
            return requests.get(url, proxies=_proxies_for(use_proxy), headers=headers, **kwargs)
        except RETRYABLE_ERRORS as error:
            if attempt >= retries:
                raise
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logging.info("请求 %s 失败（%s），%.0f 秒后重试（第 %s 次）",
                         url, type(error).__name__, wait, attempt + 1)
            attempt += 1
            time.sleep(wait)


def _proxies_for(use_proxy: bool) -> Optional[dict]:
    """`use_proxy` + 配置里的 `proxies` → requests 的 proxies 参数（没配就是 None）。"""
    if not use_proxy:
        return None
    proxy = get_config().proxies
    if not proxy:
        return None
    return {"https": proxy, "http": proxy}


def _is_fake_ip(address: str) -> bool:
    """这个 IP 是不是落在代理 / 加速器的 fake-IP 段里（198.18.0.0/15）。"""
    try:
        parts = [int(piece) for piece in str(address).split(".")]
    except (TypeError, ValueError):
        return False
    if len(parts) != 4:
        return False
    value = (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
    for low, high in FAKE_IP_RANGES:
        start = _ip_to_int(low)
        end = _ip_to_int(high)
        if start <= value <= end:
            return True
    return False


def _ip_to_int(address: str) -> int:
    parts = [int(piece) for piece in address.split(".")]
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def resolve_ip(host: str) -> str:
    """域名 → 一个 IPv4 地址；解析不出来返回空串（只用来给用户提示，不参与请求）。"""
    try:
        return socket.gethostbyname(host)
    except Exception:                        # noqa: BLE001 - 解析失败只是没有提示可用
        return ""


def describe_network_error(error: BaseException, url: str = "") -> str:
    """把网络错误翻成**用户能照着做**的一句话（技术细节留在日志里）。

    用户 2026-11-02 报的巴哈姆特 `SSLError(UNEXPECTED_EOF_WHILE_READING)` 就是被代理的
    fake-IP 接管之后那条路又不通：先说清楚「这是本机代理的问题」，再给出怎么改。
    """
    host = ""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
    except Exception:                        # noqa: BLE001
        host = ""
    text = f"{type(error).__name__}: {error}"
    network_like = isinstance(error, (requests.exceptions.RequestException, OSError))
    ssl_like = isinstance(error, requests.exceptions.SSLError) or "SSL" in text
    address = resolve_ip(host) if (host and network_like) else ""
    fake_ip = bool(address) and _is_fake_ip(address)
    if fake_ip:
        return (f"读不到 {host}：这个域名被解析到 {address}，是代理 / 加速器的 fake-IP —— "
                "代理没连通或节点挂了。请检查 config.yaml 里的 proxies（或代理软件里给它加一条"
                "直连 / 换一个可用节点）后重试；也可以先在浏览器打开页面，手动把歌词粘进来")
    if ssl_like:
        return (f"读不到 {host}：TLS 握手被中断。多半是代理 / 防火墙拦截，或站点只认浏览器指纹 —— "
                "检查 config.yaml 里的 proxies，或直接在浏览器打开页面手动粘歌词")
    if isinstance(error, requests.exceptions.Timeout) or "timed out" in text.lower():
        return f"读不到 {host}：连接超时。检查网络 / 代理，稍后重试"
    if isinstance(error, requests.exceptions.ConnectionError):
        return f"读不到 {host}：连不上。检查网络 / 代理后重试"
    return f"读取失败：{error}"
