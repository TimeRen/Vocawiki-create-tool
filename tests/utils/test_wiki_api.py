"""utils/wiki_api.py 里「问站点要中文写法」那部分的测试（不联网，HTTP 全部 mock）。

`simplified()` 给同名条目的 P主名用：站点自己的变体转换最清楚日文汉字该怎么写
（实测 `鶴三` → `鹤三`、`稲葉曇` → `稻叶昙`），工具不再自造转换表（用户 2026-10-15）。
"""
from unittest import TestCase
from unittest import mock

from utils import login, wiki_api


class _Session:
    """只会返回一个 payload 的假会话。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.params = []

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        self.params.append(dict(params or {}))
        return mock.Mock(json=lambda: self.payload)


def _parse_payload(html):
    """`action=parse&prop=text` 的返回形状。"""
    return {"parse": {"title": "沙盒", "text": html}}


class PlainTextTest(TestCase):
    def test_strips_tags_and_collapses_whitespace(self):
        self.assertEqual("鹤三", wiki_api.plain_text("<p>鹤三\n</p>"))
        self.assertEqual("40mP", wiki_api.plain_text('<div class="x">40mP</div>'))
        self.assertEqual("", wiki_api.plain_text(""))

    def test_unescapes_html_entities(self):
        self.assertEqual("A&B", wiki_api.plain_text("<p>A&amp;B</p>"))


class SimplifiedTest(TestCase):
    def setUp(self):
        wiki_api._simplified_cache.clear()

    def _call(self, text, payload=None, session=None):
        session = session or _Session(payload if payload is not None
                                      else _parse_payload(f"<p>{text}</p>"))
        with mock.patch.object(login, "get_api_session", return_value=session), \
             mock.patch.object(login, "api_url", return_value="https://voca.wiki/api.php"):
            return wiki_api.simplified(text), session

    def test_converts_with_the_site_variant(self):
        """`variant=zh-hans` 走站点自己的转换表 —— 站上怎么写就用哪个。"""
        converted, session = self._call("鶴三", _parse_payload("<p>鹤三</p>"))
        self.assertEqual("鹤三", converted)
        self.assertEqual(1, session.calls)
        self.assertEqual("zh-hans", session.params[0]["variant"])
        self.assertEqual("parse", session.params[0]["action"])
        self.assertEqual("鶴三", session.params[0]["text"])

    def test_ascii_text_does_not_ask_the_site(self):
        converted, session = self._call("kz")
        self.assertEqual("kz", converted)
        self.assertEqual(0, session.calls)

    def test_empty_text_does_not_ask_the_site(self):
        converted, session = self._call("   ")
        self.assertEqual("", converted)
        self.assertEqual(0, session.calls)

    def test_result_is_cached(self):
        session = _Session(_parse_payload("<p>鹤三</p>"))
        with mock.patch.object(login, "get_api_session", return_value=session), \
             mock.patch.object(login, "api_url", return_value="https://voca.wiki/api.php"):
            self.assertEqual("鹤三", wiki_api.simplified("鶴三"))
            self.assertEqual("鹤三", wiki_api.simplified("鶴三"))
        self.assertEqual(1, session.calls)

    def test_failure_returns_the_text_and_is_not_cached(self):
        session = mock.Mock()
        session.get.side_effect = OSError("boom")
        with mock.patch.object(login, "get_api_session", return_value=session), \
             mock.patch.object(login, "api_url", return_value="https://voca.wiki/api.php"):
            self.assertEqual("鶴三", wiki_api.simplified("鶴三"))
        self.assertNotIn("鶴三", wiki_api._simplified_cache)

    def test_empty_parse_output_keeps_the_text(self):
        converted, _ = self._call("鶴三", _parse_payload(""))
        self.assertEqual("鶴三", converted)
