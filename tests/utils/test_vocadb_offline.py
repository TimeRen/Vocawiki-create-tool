"""「连不上 VocaDB」不该把生成流程带堆栈打断（回归）。

用户 2026-11 报：生成条目时直接弹出
    HTTPSConnectionPool(host='vocadb.net', port=443): Max retries exceeded with url: /api/songs?…
    (Caused by ConnectTimeoutError(… 'Connection to vocadb.net timed out. (connect timeout=30)'))
—— 那是 `requests` 把网络层的超时原样抛了出来，整个生成流程就断在那儿（界面上就是这一串）。

现在的行为：连不上先重试几次 → 再借本机浏览器（它走**系统**代理，常能通）→ 才抛
`VocadbOffline`（一句能看懂的话，带原因与处理办法），并且连不上之后短时间内不再硬试
（否则批量生成时每个歌名都白等三次超时）。「被 Cloudflare 挡」（403）那条老路不能受影响。
"""
import os
import time
from unittest import TestCase, mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"

import requests                                                    # noqa: E402

from tests.utils.test_vocadb_song import CacheIsolationMixin        # noqa: E402
from utils import vocadb                                            # noqa: E402


def _timeout():
    """`requests` 连接超时的样子：`str()` 就是用户看到的那串 `Max retries exceeded`。"""
    return requests.exceptions.ConnectTimeout(
        "HTTPSConnectionPool(host='vocadb.net', port=443): Max retries exceeded with url: "
        "/api/songs?query=6 (Caused by ConnectTimeoutError(... 'Connection to vocadb.net timed "
        "out. (connect timeout=30)'))")


def _response(text='{"items": []}', status=200):
    """够 `_is_challenge()` / `raise_for_status()` / `.text` 用的假响应。"""
    response = mock.Mock()
    response.status_code = status
    response.text = text
    response.raise_for_status = mock.Mock()
    return response


class OfflineTest(CacheIsolationMixin, TestCase):
    """连接超时的重试 / 兜底 / 避让。"""

    def setUp(self):
        self._isolate_cache()
        # 重试前的等待与限速都别真的睡（`vocadb.time` 就是标准库的 time 模块）
        sleep = mock.patch.object(vocadb.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        before = mock.patch.object(vocadb, "_before_request")
        before.start()
        self.addCleanup(before.stop)

    def test_a_transient_timeout_is_retried(self):
        with mock.patch.object(vocadb, "http_get",
                               side_effect=[_timeout(), _response()]) as http_get:
            response = vocadb.vocadb_get("https://vocadb.net/api/songs")
        self.assertEqual(2, http_get.call_count)
        self.assertEqual('{"items": []}', response.text)
        self.assertEqual(0.0, vocadb._offline_until)      # 通了就不该再避让

    def test_exhausted_retries_explain_what_to_check(self):
        with mock.patch.object(vocadb, "http_get", side_effect=_timeout()) as http_get, \
             mock.patch.object(vocadb, "_browser_text", return_value=None):
            with self.assertRaises(vocadb.VocadbOffline) as caught:
                vocadb.vocadb_get("https://vocadb.net/api/songs")
        self.assertEqual(vocadb.VOCADB_CONNECT_ATTEMPTS, http_get.call_count)
        message = str(caught.exception)
        self.assertIn("连不上 VocaDB", message)
        self.assertIn("proxies", message)
        self.assertIn("Max retries exceeded", message)     # 原始错误留着，方便排查
        self.assertGreater(vocadb._offline_until, time.time())

    def test_the_browser_takes_over_when_the_network_is_down(self):
        with mock.patch.object(vocadb, "http_get", side_effect=_timeout()), \
             mock.patch.object(vocadb, "_browser_text",
                               return_value='{"items": [1]}') as browser:
            response = vocadb.vocadb_get("https://vocadb.net/api/songs", params={"query": "6"})
        self.assertEqual('{"items": [1]}', response.text)
        self.assertEqual(200, response.status_code)        # 伪装成正常响应，后面的流程照旧
        browser.assert_called_once()

    def test_recent_offline_skips_the_slow_retries(self):
        vocadb._offline_until = time.time() + 60
        vocadb._offline_key = vocadb._proxy_key()
        with mock.patch.object(vocadb, "http_get") as http_get, \
             mock.patch.object(vocadb, "_browser_text", return_value='{"items": []}'):
            response = vocadb.vocadb_get("https://vocadb.net/api/songs")
        http_get.assert_not_called()                       # 别再干等三个 30 秒超时
        self.assertEqual('{"items": []}', response.text)

    def test_recent_offline_without_browser_raises_at_once(self):
        vocadb._offline_until = time.time() + 60
        vocadb._offline_key = vocadb._proxy_key()
        vocadb._offline_error = "ConnectTimeout"
        with mock.patch.object(vocadb, "http_get") as http_get, \
             mock.patch.object(vocadb, "_browser_text", return_value=None):
            with self.assertRaises(vocadb.VocadbOffline) as caught:
                vocadb.vocadb_get("https://vocadb.net/api/songs")
        http_get.assert_not_called()
        self.assertIn("ConnectTimeout", str(caught.exception))

    def test_switching_proxy_ends_the_offline_window(self):
        vocadb._offline_until = time.time() + 60
        vocadb._offline_key = "http://127.0.0.1:7890"
        with mock.patch.object(vocadb, "_proxy_key", return_value=""):
            self.assertFalse(vocadb._offline_cooling_down())

    def test_only_connection_problems_count_as_offline(self):
        self.assertTrue(vocadb._is_network_error(_timeout()))
        self.assertTrue(vocadb._is_network_error(
            requests.exceptions.ConnectionError("Connection aborted.")))
        # 拿到了响应（状态码不对）不是「连不上」：那是 Cloudflare / 404 那条路的事
        self.assertFalse(vocadb._is_network_error(requests.exceptions.HTTPError("404")))
        self.assertFalse(vocadb._is_network_error(ValueError("Expecting value")))

    def test_a_challenge_is_still_handled_as_blocked(self):
        challenge = _response("Just a moment...", status=403)
        with mock.patch.object(vocadb, "http_get", return_value=challenge) as http_get, \
             mock.patch.object(vocadb, "_browser_text", return_value=None), \
             mock.patch.object(vocadb, "_log_block"):
            with self.assertRaises(vocadb.VocadbBlocked) as caught:
                vocadb.vocadb_get("https://vocadb.net/api/songs")
        self.assertNotIsInstance(caught.exception, vocadb.VocadbOffline)
        self.assertEqual(2, http_get.call_count)           # 首次 + 挑战后重试一次
        self.assertEqual(0.0, vocadb._offline_until)       # 不能把「被挡」记成「连不上」

    def test_offline_does_not_ask_for_pasted_json(self):
        """连不上时让人去粘 JSON 没意义（那个页面同样打不开）—— 直接把原因说出来。"""
        offline = vocadb.VocadbOffline("连不上 VocaDB")
        with mock.patch.object(vocadb, "cached_song_id", return_value=None), \
             mock.patch.object(vocadb, "search_song_id", side_effect=offline), \
             mock.patch.object(vocadb, "_prompt_blocked_json") as prompt:
            with self.assertRaises(vocadb.VocadbOffline):
                vocadb._details_payload("テスト")
        prompt.assert_not_called()


class ProducerRequestMessageTest(TestCase):
    """P主模板那条路（`utils/producer_template._get_json`）也不能甩 `requests` 的原文。"""

    def test_exhausted_retries_explain_what_to_check(self):
        from utils import producer_template
        with mock.patch.object(producer_template, "http_get", side_effect=_timeout()), \
             mock.patch.object(producer_template.time, "sleep"):
            with self.assertRaises(RuntimeError) as caught:
                producer_template._get_json("https://vocadb.net/api/songs", retries=1)
        message = str(caught.exception)
        self.assertIn("连不上 VocaDB", message)
        self.assertIn("proxies", message)
