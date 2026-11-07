from unittest import TestCase, mock

import requests

from utils import helpers
from utils.string import group_number, split_number


class SplitNumberTest(TestCase):
    """数值写法按《格式手册/官方材料》·数值：**五位及以上**才用半角逗号分节。"""

    def test_split_number(self):
        self.assertEqual("123", split_number(123))
        self.assertEqual("4321", split_number(4321))          # 四位数不分节
        self.assertEqual("9999", split_number(9999))
        self.assertEqual("10,000", split_number(10000))       # 五位数起才分节
        self.assertEqual("54,321", split_number(54321))
        self.assertEqual("123,456", split_number(123456))     # 以前会被写成 123,000+
        self.assertEqual("1,234,567", split_number(1234567))

    def test_group_number_matches_split_number(self):
        for value in (0, 999, 4321, 9999, 10000, 54321, 100000, 1081622):
            self.assertEqual(split_number(value), group_number(value))


class NetworkErrorHintTest(TestCase):
    """网络错误 → 用户能照着做的一句话（用户 2026-11-02 报的巴哈姆特 `SSLError`）。"""

    @staticmethod
    def _ssl_error():
        return requests.exceptions.SSLError(
            "Max retries exceeded", OSError(8, "EOF occurred in violation of protocol"))

    def test_fake_ip_points_at_the_proxy(self):
        with mock.patch.object(helpers, "resolve_ip", return_value="198.18.0.214"):
            message = helpers.describe_network_error(
                self._ssl_error(), "https://home.gamer.com.tw/artwork.php?sn=1")
        self.assertIn("198.18.0.214", message)
        self.assertIn("fake-IP", message)
        self.assertIn("proxies", message)
        self.assertIn("home.gamer.com.tw", message)

    def test_plain_ssl_error_mentions_the_handshake(self):
        with mock.patch.object(helpers, "resolve_ip", return_value="104.18.3.197"):
            message = helpers.describe_network_error(
                self._ssl_error(), "https://home.gamer.com.tw/artwork.php?sn=1")
        self.assertIn("TLS", message)
        self.assertNotIn("fake-IP", message)

    def test_timeout(self):
        with mock.patch.object(helpers, "resolve_ip", return_value="104.18.3.197"):
            message = helpers.describe_network_error(
                requests.exceptions.Timeout("timed out"), "https://example.com/x")
        self.assertIn("超时", message)

    def test_unknown_error_keeps_the_original_text(self):
        self.assertIn("boom", helpers.describe_network_error(ValueError("boom"), ""))

    def test_fake_ip_range(self):
        self.assertTrue(helpers._is_fake_ip("198.18.0.1"))
        self.assertTrue(helpers._is_fake_ip("198.19.255.254"))
        self.assertFalse(helpers._is_fake_ip("198.20.0.1"))
        self.assertFalse(helpers._is_fake_ip("104.18.3.197"))
        self.assertFalse(helpers._is_fake_ip(""))


class HttpGetRetryTest(TestCase):
    def test_retries_a_broken_handshake(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(helpers, "time") as fake_time, \
             mock.patch.object(helpers.requests, "get",
                               side_effect=[requests.exceptions.SSLError("eof"), response]) as get:
            self.assertIs(response, helpers.http_get("https://x/y", use_proxy=False, retries=1))
        self.assertEqual(2, get.call_count)
        fake_time.sleep.assert_called()

    def test_gives_up_after_the_last_retry(self):
        with mock.patch.object(helpers, "time"), \
             mock.patch.object(helpers.requests, "get",
                               side_effect=requests.exceptions.SSLError("eof")) as get:
            with self.assertRaises(requests.exceptions.SSLError):
                helpers.http_get("https://x/y", use_proxy=False, retries=2)
        self.assertEqual(3, get.call_count)

    def test_no_retry_by_default(self):
        with mock.patch.object(helpers.requests, "get",
                               side_effect=requests.exceptions.SSLError("eof")) as get:
            with self.assertRaises(requests.exceptions.SSLError):
                helpers.http_get("https://x/y", use_proxy=False)
        self.assertEqual(1, get.call_count)

    def test_proxy_follows_the_config(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(helpers, "get_config") as config, \
             mock.patch.object(helpers.requests, "get", return_value=response) as get:
            config.return_value.proxies = "http://127.0.0.1:7897"
            helpers.http_get("https://x/y", use_proxy=True)
            self.assertEqual({"https": "http://127.0.0.1:7897", "http": "http://127.0.0.1:7897"},
                             get.call_args.kwargs["proxies"])
            helpers.http_get("https://x/y", use_proxy=False)
            self.assertIsNone(get.call_args.kwargs["proxies"])
