"""登录 Vocawiki（MediaWiki），维护全局会话与 CSRF token。"""
import logging
from urllib.parse import urlsplit

import requests

from config.config import get_config, get_wiki_credentials
from utils import identity

API_DEFAULT = "https://voca.wiki/api.php"
# 工具自己的 UA（写着仓库地址，见 utils/identity.py）。注意它**只该发给 wiki 站点**：
# 发到别人家的站点上，人家会以为是 voca.wiki 官方在发请求——WikiSession 负责把关。
USER_AGENT = identity.USER_AGENT

_session = None
_csrf_token = None
_username = None            # 登录成功后的用户名（界面要显示「已登录：xxx」）

# 匿名用户的 CSRF token 是站点上的常量 `+\`：拿到它就说明**会话已经掉线**，
# 这不是「token 过期」，只能重新登录（见 `refresh_csrf_token()`）。
ANON_CSRF_TOKEN = "+\\"

# 站点说「这个 token 不认」的错误码：
# `badtoken` = 会话里的 token 换过了（重新登录过 / 站点把它轮换了）；
# `notloggedin` / `assertuserfailed` / `mustbeloggedin` = 会话整个掉了。
# 三种都能靠「刷新 token（必要时重新登录）+ 重发一次」救回来，见 `ensure_writable()`。
TOKEN_ERROR_CODES = ("badtoken", "notloggedin", "assertuserfailed", "mustbeloggedin")


def _wiki_host() -> str:
    """配置里那个 wiki 的主机名（读不到配置就返回空串）。"""
    try:
        return (urlsplit(_api_url()).hostname or "").lower()
    except Exception as e:                          # noqa: BLE001 - 读不到就当不知道
        logging.debug("取 wiki 主机名失败：%s", e)
        return ""


class WikiSession(requests.Session):
    """带「UA 分主机」规则的会话。

    * 请求发往配置里的 wiki（含同站的头像接口）→ 用工具自己的 `USER_AGENT`；
    * 万一哪天被拿去请求别的域名 → 换成普通浏览器 UA（并记一条警告），
      绝不让「Vocawiki-create-tool (+仓库地址)」这个身份跑到第三方站点的日志里。

    默认 UA 就在 `__init__` 里设好（不依赖调用方记得设），
    所以像 `utils/voca.py::ProducerSearchSession` 那样直接构造的子类也守同一条规矩。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers["User-Agent"] = USER_AGENT

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        prepared = super().prepare_request(request)
        user_agent = prepared.headers.get("User-Agent", "")
        if _is_wiki_url(prepared.url):
            prepared.headers["User-Agent"] = USER_AGENT
        elif user_agent == USER_AGENT:
            prepared.headers["User-Agent"] = identity.BROWSER_USER_AGENT
            logging.warning("对 %s 的请求不该带工具自己的 UA，已换成普通浏览器 UA",
                            urlsplit(prepared.url).hostname or prepared.url)
        return prepared


def _is_wiki_url(url: str) -> bool:
    """这个地址是不是发往我们自己的 wiki（按主机名比）。"""
    host = _wiki_host()
    return bool(host) and (urlsplit(url or "").hostname or "").lower() == host


def _new_session() -> requests.Session:
    session = WikiSession()          # UA 在 WikiSession.__init__ 里设（工具自己的那条）
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


def current_user() -> str:
    """已登录时返回用户名，否则空串（不做网络请求，用登录时记下的名字）。"""
    return _username if is_logged_in() else ""


def wiki_username(login_name: str) -> str:
    """登录名 → **站点上真正认识的那个用户名**。

    登录时写的是凭据名：**机器人密码**是 `账户名@机器人名`，CentralAuth 的全局账号写作
    `名字@wiki`——`@` 后面那截只说明「用哪个凭据 / 哪个 wiki」，站点上没有叫这个名字的用户。
    2026-09 用户用「人间百态@create」登录，头像一直出不来就是这么回事：
    `list=users&ususers=人间百态@create` 返回 missing、`avatar.php?user=人间百态@create`
    302 到站点默认头像（而那张默认图在 Cloudflare 后面，403 下不来）；
    去掉后缀的「人间百态」是 userid 28，有真头像。
    MediaWiki 用户名里不允许出现 `@`，所以按第一个 `@` 切就行。
    """
    name = str(login_name or "").strip()
    head = name.split("@", 1)[0].strip()
    return head or name


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


def _fetch_csrf_token(session: requests.Session) -> str:
    """向站点要一个 CSRF token（匿名会话会拿到 `ANON_CSRF_TOKEN`）。"""
    response = session.get(_api_url(), params={
        "action": "query",
        "meta": "tokens",
        "type": "csrf",
        "format": "json",
    }, timeout=30)
    response.raise_for_status()
    return str(response.json()["query"]["tokens"]["csrftoken"])


def refresh_csrf_token() -> str:
    """重新问站点要一个 CSRF token；拿不到 / 会话已掉线时返回空串。

    写操作报 `badtoken` 时用（用户 2026-10-22 报的「上传第二个条目报 Invalid CSRF token」：
    手里那个 token 是上次登录时取的，中途站点把它轮换了，于是第二个条目一提交就被拒）。
    """
    global _csrf_token
    if _session is None:
        return ""
    try:
        token = _fetch_csrf_token(_session)
    except Exception as e:                          # noqa: BLE001 - 网络抽风就交给调用方重试
        logging.warning("重新获取 CSRF token 失败：%s", e)
        return ""
    if token == ANON_CSRF_TOKEN:
        # 会话掉了：token 是匿名的那一个，得重新登录（界面上的登录状态也跟着变回未登录）
        logging.warning("Vocawiki 会话已失效（拿到的是匿名 token），需要重新登录")
        _csrf_token = None
        return ""
    if token != _csrf_token:
        logging.info("已刷新 Vocawiki 的 CSRF token")
    _csrf_token = token
    return token


def relogin() -> bool:
    """用 wiki_credentials.yaml 里的账号重新登录（会话掉线后由写操作自动调用）。"""
    try:
        username, password = get_wiki_credentials()
    except Exception as e:                          # noqa: BLE001 - 读不到凭据就是登录不了
        logging.warning("读取 Vocawiki 凭据失败，无法重新登录：%s", e)
        return False
    if not username or not password:
        logging.warning("还没配置 Vocawiki 凭据，无法重新登录（请到「设置」页的「账号与密钥」填写）")
        return False
    logging.info("Vocawiki 登录状态失效，正在用保存的账号重新登录：%s", wiki_username(username))
    return login(username, password)


def ensure_writable() -> bool:
    """确保手里有一个能用的 CSRF token：先刷新，会话掉线就重新登录一次。"""
    if refresh_csrf_token():
        return True
    return relogin() and _csrf_token is not None


def refresh_token_in(data: dict) -> bool:
    """写操作的请求体被站点以 token 错误拒掉后调用：修好登录状态并把新 token 写回 `data`。

    返回 True 表示可以拿 `data` 原样重发一次。
    """
    if not ensure_writable():
        return False
    data["token"] = _csrf_token
    return True


def is_token_error(payload) -> bool:
    """站点这次返回的是不是「token 不认」这类错误。"""
    if not isinstance(payload, dict):
        return False
    error = payload.get("error") or {}
    return str(error.get("code") or "").strip().lower() in TOKEN_ERROR_CODES


def login(username: str, password: str) -> bool:
    """使用账号/机器人密码登录 Vocawiki，并缓存会话与 CSRF token。"""
    global _session, _csrf_token, _username
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
    _csrf_token = _fetch_csrf_token(session)
    _session = session
    # 存**站点上的**用户名（去掉 `@机器人名` / `@wiki` 后缀）：界面显示、查头像都用它
    _username = wiki_username(username)
    if _username != username:
        logging.info("登录名 %s 带凭据后缀，站点上的用户名按 %s 用", username, _username)
    logging.info("Logged in to Vocawiki as %s", _username)
    return True


def logout() -> bool:
    """退出登录：请服务端登出，并清掉本地会话（无论请求成不成功，会话一律清掉）。"""
    global _session, _csrf_token, _username
    if _session is None:
        _username = None
        return True
    try:
        response = _session.get(_api_url(), params={
            "action": "query", "meta": "tokens", "type": "login", "format": "json",
        }, timeout=30)
        token = response.json()["query"]["tokens"]["logintoken"]
        _session.post(_api_url(), data={
            "action": "logout", "token": token, "format": "json",
        }, timeout=30)
        logging.info("Logged out of Vocawiki.")
    except Exception as e:                       # noqa: BLE001 - 服务端不配合也要退
        logging.warning("退出登录的请求没成功，本地会话照样清掉：%s", e)
    finally:
        _session = None
        _csrf_token = None
        _username = None
    return True


def main() -> None:
    if is_logged_in():                           # 界面上提前登录过就不用再来一次
        return
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
    if is_logged_in():
        logging.debug("已经登录过 Vocawiki（%s），跳过重复登录。", current_user())
        return True
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
