"""对外请求的 User-Agent 规则（`utils/login.py` + `utils/helpers.http_get`）。

背景（用户 2026-09 转来的反馈）：工具原来的 UA 是
`Vocawiki-create-tool/1.0 (https://voca.wiki)` —— URL 写的是 wiki 而不是仓库，
别人在站点日志里看到既找不到作者，还容易以为是 voca.wiki 官方在发请求。

现在的规矩：
* 发往 **Vocawiki** 的请求（`WikiSession`）：用工具自己的 UA（写着仓库地址），**不降级**；
* 发往**其它外部网站**的请求（`helpers.http_get`）：**一律用普通浏览器 UA** ——
  工具身份只写给 Vocawiki，别人的站点没必要知道是哪个程序在抓数据
  （用户 2026-09 明确要求「站外还是用浏览器 UA 吧」，顺带省掉那次「先被挡再重试」）。
"""
from unittest import TestCase, mock

import requests

from utils import helpers, identity, login


def _request_user_agent(session, url: str, headers=None) -> str:
    """不发请求，只算出这次请求真正会带出去的 User-Agent。"""
    request = requests.Request("GET", url, headers=headers)
    return session.prepare_request(request).headers.get("User-Agent", "")


def _response(status: int = 200, text: str = "ok"):
    """假响应（`ok` 按 HTTP 语义算，`http_get` 就是看它决定要不要降级）。"""
    response = mock.Mock(spec=requests.Response)
    response.status_code = status
    response.ok = 200 <= status < 400
    response.text = text
    return response


class IdentityTest(TestCase):
    def test_tool_user_agent_points_at_the_repo(self):
        self.assertIn(identity.APP_NAME, identity.USER_AGENT)
        self.assertIn(identity.REPO_URL, identity.USER_AGENT)
        self.assertIn(identity.VERSION, identity.USER_AGENT)

    def test_tool_user_agent_does_not_claim_to_be_the_wiki(self):
        """UA 里不能出现 voca.wiki：那是在替站点「代言」。"""
        self.assertNotIn("voca.wiki", identity.USER_AGENT)

    def test_browser_user_agent_is_a_plain_chrome(self):
        self.assertTrue(identity.BROWSER_USER_AGENT.startswith("Mozilla/5.0"))
        self.assertNotIn(identity.APP_NAME, identity.BROWSER_USER_AGENT)


class LoginNameTest(TestCase):
    """登录名不是站点上的用户名：`账户名@机器人名`（或 `名字@wiki`）要先把后缀去掉。

    用户 2026-09 用「人间百态@create」登录，头像一直出不来就是这么回事：带着后缀去
    `list=users` / `avatar.php` 查询，站点都当没这个人。
    """

    def test_bot_password_suffix_is_stripped(self):
        self.assertEqual("人间百态", login.wiki_username("人间百态@create"))
        self.assertEqual("TimeRen", login.wiki_username("TimeRen@MyBot@extra"))

    def test_plain_name_is_untouched(self):
        self.assertEqual("人间百态", login.wiki_username("人间百态"))
        self.assertEqual("TimeRen", login.wiki_username("  TimeRen "))
        self.assertEqual("", login.wiki_username(""))
        self.assertEqual("@bot", login.wiki_username("@bot"), "只剩后缀就照原样用，别变成空串")

    def test_login_keeps_only_the_site_username(self):
        """`login()` 成功后 `current_user()` 必须是站点上的名字（头像、显示都靠它）。"""
        answers = [{"query": {"tokens": {"logintoken": "LT"}}},
                   {"login": {"result": "Success"}},
                   {"query": {"tokens": {"csrftoken": "CT"}}}]
        session = mock.Mock()
        session.get.side_effect = [mock.Mock(json=lambda a=answers[0]: a),
                                   mock.Mock(json=lambda a=answers[2]: a)]
        session.post.return_value = mock.Mock(json=lambda a=answers[1]: a)
        # 登录状态是模块级全局变量，用完清掉（别真的调 logout()，那会再发请求）
        for name in ("_session", "_csrf_token", "_username"):
            self.addCleanup(setattr, login, name, None)
        with mock.patch.object(login, "_new_session", return_value=session):
            self.assertTrue(login.login("人间百态@create", "机器人密码"))
        self.assertEqual("人间百态", login.current_user())


class CsrfTokenRepairTest(TestCase):
    """写操作被站点以「token 不认」拒掉时，能自己把登录状态修好。

    用户 2026-10-22 报：上传第二个条目时报 `Invalid CSRF token.`，重新登录才提交得上去。
    手里那个 token 是登录时取的，中途站点把它轮换了（或会话掉了），
    所以写操作要能「刷新 token（必要时重新登录）→ 重发一次」。
    """

    def setUp(self):
        patcher = mock.patch.object(login, "_api_url", return_value="https://voca.wiki/api.php")
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("_session", None), ("_csrf_token", None), ("_username", None)):
            self.addCleanup(setattr, login, name, value)
        login._session = mock.Mock()
        login._csrf_token = "OLD"

    def _respond(self, token):
        login._session.get.return_value = mock.Mock(
            json=lambda: {"query": {"tokens": {"csrftoken": token}}})

    def test_refresh_picks_up_the_sites_new_token(self):
        self._respond("NEW")
        self.assertEqual("NEW", login.refresh_csrf_token())
        self.assertEqual("NEW", login.get_csrf_token())

    def test_anonymous_token_means_the_session_is_gone(self):
        self._respond(login.ANON_CSRF_TOKEN)
        with self.assertLogs(level="WARNING"):
            self.assertEqual("", login.refresh_csrf_token())
        self.assertFalse(login.is_logged_in(), "会话掉了，界面上也不该再显示已登录")

    def test_network_failure_keeps_the_old_token(self):
        login._session.get.side_effect = OSError("boom")
        with self.assertLogs(level="WARNING"):
            self.assertEqual("", login.refresh_csrf_token())
        self.assertEqual("OLD", login.get_csrf_token())

    def test_no_session_means_nothing_to_refresh(self):
        login._session = None
        self.assertEqual("", login.refresh_csrf_token())

    def test_relogin_uses_the_saved_credentials(self):
        seen = {}

        def fake_login(username, password):
            seen["args"] = (username, password)
            login._csrf_token = "FRESH"
            return True

        with mock.patch.object(login, "get_wiki_credentials", return_value=("人间百态@create", "pw")), \
                mock.patch.object(login, "login", side_effect=fake_login):
            self.assertTrue(login.relogin())
        self.assertEqual(("人间百态@create", "pw"), seen["args"])

    def test_relogin_gives_up_without_credentials(self):
        with mock.patch.object(login, "get_wiki_credentials", return_value=("", "")), \
                self.assertLogs(level="WARNING"):
            self.assertFalse(login.relogin())

    def test_ensure_writable_relogs_in_after_a_dead_session(self):
        login._session.get.side_effect = OSError("boom")

        def fake_relogin():
            login._csrf_token = "FRESH"
            return True

        with mock.patch.object(login, "relogin", side_effect=fake_relogin) as relogin:
            self.assertTrue(login.ensure_writable())
        relogin.assert_called_once()
        self.assertEqual("FRESH", login.get_csrf_token())

    def test_ensure_writable_reports_failure(self):
        with mock.patch.object(login, "refresh_csrf_token", return_value=""), \
                mock.patch.object(login, "relogin", return_value=False):
            self.assertFalse(login.ensure_writable())

    def test_refresh_token_in_rewrites_the_payload(self):
        self._respond("NEW")
        data = {"token": "OLD"}
        self.assertTrue(login.refresh_token_in(data))
        self.assertEqual("NEW", data["token"])

    def test_refresh_token_in_leaves_the_payload_alone_on_failure(self):
        data = {"token": "OLD"}
        with mock.patch.object(login, "ensure_writable", return_value=False):
            self.assertFalse(login.refresh_token_in(data))
        self.assertEqual("OLD", data["token"])

    def test_is_token_error(self):
        for code in ("badtoken", "notloggedin", "assertuserfailed", "mustbeloggedin", "Badtoken"):
            with self.subTest(code=code):
                self.assertTrue(login.is_token_error({"error": {"code": code}}))
        for payload in ({"error": {"code": "protectedpage"}}, {}, None):
            with self.subTest(payload=payload):
                self.assertFalse(login.is_token_error(payload))


class WikiSessionTest(TestCase):
    def setUp(self):
        self.session = login._new_session()
        patcher = mock.patch.object(login, "_wiki_host", return_value="voca.wiki")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_session_is_a_wiki_session(self):
        self.assertIsInstance(self.session, login.WikiSession)

    def test_tool_ua_goes_to_the_wiki_api(self):
        self.assertEqual(identity.USER_AGENT,
                         _request_user_agent(self.session, "https://voca.wiki/api.php"))

    def test_tool_ua_goes_to_the_avatar_endpoint_on_the_same_site(self):
        """头像走的是 api.php 之外的入口，同站，可以用工具 UA。"""
        self.assertEqual(
            identity.USER_AGENT,
            _request_user_agent(
                self.session, "https://voca.wiki/extensions/Avatar/avatar.php?res=128"))

    def test_other_sites_never_get_the_tool_ua(self):
        for url in ("https://www.nicovideo.jp/watch/sm43439171",
                    "https://vocadb.net/api/songs/589466/details",
                    "https://img.youtube.com/vi/P609XPR0mSg/maxresdefault.jpg",
                    "https://www.nicolog.jp/watch/sm43439171",
                    "https://api.bilibili.com/x/web-interface/view?bvid=BV1DH4y1E7aA"):
            with self.subTest(url=url):
                self.assertEqual(identity.BROWSER_USER_AGENT,
                                 _request_user_agent(self.session, url))

    def test_swapping_is_logged(self):
        with self.assertLogs("root", level="WARNING") as logs:
            _request_user_agent(self.session, "https://vocadb.net/api/songs")
        self.assertIn("vocadb.net", "\n".join(logs.output))

    def test_callers_own_user_agent_is_respected(self):
        """别的调用方显式指定的 UA 不动（这里只负责别把**工具**的 UA 漏出去）。"""
        self.assertEqual(
            "Something/1.0",
            _request_user_agent(self.session, "https://example.com/x",
                                {"User-Agent": "Something/1.0"}))

    def test_unknown_wiki_host_keeps_the_browser_ua(self):
        """连 wiki 主机名都拿不到时，宁可退化也不要漏出工具身份。"""
        with mock.patch.object(login, "_wiki_host", return_value=""):
            self.assertEqual(identity.BROWSER_USER_AGENT,
                             _request_user_agent(self.session, "https://voca.wiki/api.php"))


class HttpGetUserAgentTest(TestCase):
    """站外抓取：**一律**用浏览器 UA，工具 UA 只发给 Vocawiki（用户 2026-09 改的规矩）。"""

    def setUp(self):
        self.calls = []
        self.responses = []
        self.get = mock.Mock(side_effect=self._fake_get)
        patcher = mock.patch.object(helpers.requests, "get", self.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        config = mock.patch.object(helpers, "get_config")
        self.config = config.start()
        self.config.return_value.proxies = None
        self.addCleanup(config.stop)

    def _fake_get(self, url, **kwargs):
        # headers 要拷贝一份：实现里那份 dict 别被后续改动影响
        record = {**kwargs, "headers": dict(kwargs.get("headers") or {})}
        self.calls.append({"url": url, **record})
        return self.responses[len(self.calls) - 1]

    def _user_agents(self):
        return [call["headers"].get("User-Agent") for call in self.calls]

    def test_browser_user_agent_is_used(self):
        self.responses = [_response()]
        helpers.http_get("https://vocadb.net/api/songs", use_proxy=True)
        self.assertEqual([identity.BROWSER_USER_AGENT], self._user_agents())

    def test_tool_user_agent_never_leaves_the_wiki(self):
        """站外一个字节都不许带工具 UA（对方站点日志里不该出现这个身份）。"""
        self.responses = [_response(), _response(), _response()]
        for url in ("https://www.nicovideo.jp/watch/sm1", "https://vocadb.net/api/songs",
                    "https://www.bilibili.com/video/BV1"):
            helpers.http_get(url, use_proxy=True)
        self.assertNotIn(identity.USER_AGENT, self._user_agents())

    def test_blocked_request_is_not_retried(self):
        """被挡了也不重试：UA 本来就是浏览器 UA，再发一次不会有别的结果。"""
        self.responses = [_response(429)]
        self.assertEqual(429, helpers.http_get("https://example.com/x",
                                               use_proxy=False).status_code)
        self.assertEqual(1, len(self.calls))

    def test_refused_connection_propagates(self):
        """连接被掐就抛给调用方，不再换个 UA 偷偷重试一次。"""
        self.get.side_effect = requests.exceptions.SSLError("EOF occurred in violation")
        with self.assertRaises(requests.exceptions.SSLError):
            helpers.http_get("https://www.nicovideo.jp/watch/sm1", use_proxy=True)

    def test_other_headers_are_kept(self):
        """Range / Referer 这类头要在。"""
        self.responses = [_response()]
        helpers.http_get("https://example.com/img.jpg", use_proxy=False,
                         headers={"Range": "bytes=0-1023", "Referer": "https://example.com/"})
        headers = self.calls[0]["headers"]
        self.assertEqual("bytes=0-1023", headers["Range"])
        self.assertEqual("https://example.com/", headers["Referer"])
        self.assertEqual(identity.BROWSER_USER_AGENT, headers["User-Agent"])

    def test_explicit_user_agent_wins(self):
        """调用方自己指定 UA 时以它为准，本函数不再插手。"""
        self.responses = [_response(403)]
        helpers.http_get("https://example.com/x", use_proxy=False,
                         headers={"User-Agent": "Something/1.0"})
        self.assertEqual(["Something/1.0"], self._user_agents())

    def test_proxy_is_passed_through(self):
        self.config.return_value.proxies = "http://127.0.0.1:7890"
        self.responses = [_response()]
        helpers.http_get("https://example.com/x", use_proxy=True)
        self.assertEqual({"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"},
                         self.calls[0]["proxies"])
        self.calls.clear()
        self.responses = [_response()]
        helpers.http_get("https://example.com/x", use_proxy=False)
        self.assertIsNone(self.calls[0]["proxies"])


class ScraperHeaderTest(TestCase):
    """抓站那几处不再各自写死浏览器 UA，统一交给 http_get。"""

    def test_modules_do_not_hardcode_a_user_agent(self):
        from models import video
        from utils import at_wiki, image, source_filler
        for module in (video, at_wiki, image, source_filler):
            with self.subTest(module=module.__name__):
                self.assertFalse(hasattr(module, "REQUEST_HEADERS"), "别再加回写死的 UA")
                self.assertFalse(hasattr(module, "ATWIKI_HEADERS"))

    def test_source_filler_only_sends_referer(self):
        from utils import source_filler
        self.assertEqual({"Referer": "https://www.bilibili.com/"},
                         source_filler._headers("https://www.bilibili.com/"))

