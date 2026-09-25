"""登录 Vocawiki（MediaWiki），维护全局会话与 CSRF token。"""
import logging

import requests

from config.config import get_config, get_wiki_credentials

API_DEFAULT = "https://voca.wiki/api.php"
USER_AGENT = "Vocawiki-create-tool/1.0 (https://voca.wiki)"

_session = None
_csrf_token = None


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    proxies = get_config().proxies
    if proxies:
        session.proxies.update({"https": proxies, "http": proxies})
    return session


def _api_url() -> str:
    url = get_config().wiki.api_url
    return url or API_DEFAULT


def api_url() -> str:
    """Vocawiki 的 API 地址。"""
    return _api_url()


def is_logged_in() -> bool:
    """是否已成功登录并拿到 CSRF token。"""
    return _session is not None and _csrf_token is not None


def get_api_session() -> requests.Session:
    """已登录时返回登录会话，否则返回匿名会话，用于无需登录的只读接口（如 action=parse）。"""
    return _session if _session is not None else _new_session()


def get_session() -> requests.Session:
    if _session is None:
        raise RuntimeError("Not logged in to Vocawiki. Call login.main() first.")
    return _session


def get_csrf_token() -> str:
    if _csrf_token is None:
        raise RuntimeError("No CSRF token available. Call login.main() first.")
    return _csrf_token


def login(username: str, password: str) -> bool:
    """使用账号/机器人密码登录 Vocawiki，并缓存会话与 CSRF token。"""
    global _session, _csrf_token
    session = _new_session()

    # 1. 获取登录 token
    response = session.get(_api_url(), params={
        "action": "query",
        "meta": "tokens",
        "type": "login",
        "format": "json",
    }, timeout=30)
    response.raise_for_status()
    login_token = response.json()["query"]["tokens"]["logintoken"]

    # 2. 登录
    response = session.post(_api_url(), data={
        "action": "login",
        "lgname": username,
        "lgpassword": password,
        "lgtoken": login_token,
        "format": "json",
    }, timeout=30)
    response.raise_for_status()
    login_result = response.json().get("login", {})
    if login_result.get("result") != "Success":
        logging.error("Vocawiki login failed: %s", login_result)
        return False

    # 3. 获取 CSRF token（编辑/上传必需）
    response = session.get(_api_url(), params={
        "action": "query",
        "meta": "tokens",
        "type": "csrf",
        "format": "json",
    }, timeout=30)
    response.raise_for_status()
    _csrf_token = response.json()["query"]["tokens"]["csrftoken"]
    _session = session
    logging.info("Logged in to Vocawiki as %s", username)
    return True


def main() -> None:
    username, password = get_wiki_credentials()
    if not username or not password:
        raise RuntimeError(
            "请在 wiki_credentials.yaml 中填写 Vocawiki 的用户名与密码"
            "（建议使用机器人密码，用户名为 账户名@机器人名）。"
        )
    if not login(username, password):
        raise RuntimeError("Failed to log in to Vocawiki.")


def try_login() -> bool:
    """尝试登录 Vocawiki；缺少凭据或登录失败时返回 False，不抛异常。"""
    try:
        username, password = get_wiki_credentials()
        if not username or not password:
            logging.warning("未在 wiki_credentials.yaml 中配置 Vocawiki 凭据，跳过登录。")
            return False
        return login(username, password)
    except Exception as e:
        logging.warning("登录 Vocawiki 失败：%s", e)
        return False


if __name__ == "__main__":
    main()
