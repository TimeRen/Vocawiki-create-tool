import math
from typing import Union, Callable, List

import requests

from config.config import get_config
from utils import identity, ui
from utils.save_input import save_input
from utils.string import is_empty


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


def http_get(url: str, use_proxy: bool, **kwargs):
    """GET 一个**站外**地址（niconico / YouTube / bilibili / vocadb / 网易云 …）。

    UA 规则（用户 2026-09 要求）：**站外一律用普通浏览器 UA**。工具自己的 UA 只发给
    用户配置的那个 wiki（见 `utils/login.py` 的 `WikiSession`）——别人的站点不需要知道
    是哪个程序在抓数据，也就省掉「先被挡一次、再换 UA 重试」那次多余请求。
    调用方**显式**传了 `User-Agent` 的就以它为准（本函数不再插手）。

    注意：发往 Vocawiki 的请求不走这里（走 `WikiSession`，那边只用工具 UA、不降级）。
    见 `utils/identity.py` 的说明。
    """
    proxies = None
    if use_proxy and get_config().proxies:
        proxies = {
            'https': get_config().proxies,
            'http': get_config().proxies
        }
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", identity.BROWSER_USER_AGENT)
    return requests.get(url, proxies=proxies, headers=headers, **kwargs)
