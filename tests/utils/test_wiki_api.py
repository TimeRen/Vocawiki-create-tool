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


class CaseVariantTest(TestCase):
    """站上**区分大小写**：`liar` 与 `Liar` 是两个标题（`titles=liar` 回 missing）。

    用户 2026-11-10 报的《liar(zashi)》：站上已有《Liar》，工具却当成「没有同名条目」。
    """

    def test_variants_of_a_lowercase_title(self):
        self.assertEqual(["Liar", "LIAR"], wiki_api._case_variants("liar"))

    def test_variants_of_an_uppercase_title(self):
        self.assertEqual(["eden", "Eden"], wiki_api._case_variants("EDEN"))

    def test_no_variants_for_empty_or_caseless_titles(self):
        self.assertEqual([], wiki_api._case_variants(""))
        self.assertEqual([], wiki_api._case_variants("向日葵"))

    def _facts(self, payload, title, any_case=False):
        session = _Session(payload)
        with mock.patch.object(login, "get_api_session", return_value=session), \
             mock.patch.object(login, "api_url", return_value="https://voca.wiki/api.php"):
            return wiki_api.fetch_page_facts(title, any_case=any_case), session

    def test_the_variant_page_is_found_in_the_same_request(self):
        page = {"title": "Liar", "pageid": 33164, "pageprops": {},
                "revisions": [{"slots": {"main": {"content": "{{VOCALOID Songbox}}"}}}]}
        payload = {"query": {"pages": [{"title": "liar", "missing": True}, page,
                                       {"title": "LIAR", "missing": True}]}}
        facts, session = self._facts(payload, "liar", any_case=True)
        self.assertTrue(facts["exists"])
        self.assertTrue(facts["case_variant"])
        self.assertEqual("Liar", facts["title"], "要报站上真正的标题")
        self.assertIn("{{VOCALOID Songbox}}", facts["text"])
        self.assertEqual(["liar", "Liar", "LIAR"], session.params[0]["titles"].split("|"))
        self.assertEqual(1, session.calls, "大小写变体与精确标题在同一个请求里查")

    def test_nothing_exists_when_no_variant_matches(self):
        payload = {"query": {"pages": [{"title": "liar", "missing": True},
                                       {"title": "Liar", "missing": True}]}}
        facts, _ = self._facts(payload, "liar", any_case=True)
        self.assertFalse(facts["exists"])
        self.assertFalse(facts["case_variant"])

    def test_any_case_is_off_by_default(self):
        payload = {"query": {"pages": [{"title": "liar", "missing": True}]}}
        _facts, session = self._facts(payload, "liar")
        self.assertEqual("liar", session.params[0]["titles"])


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
