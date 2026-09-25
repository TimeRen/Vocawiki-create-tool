"""utils/voca.py 的 P主模板字典测试（不联网，HTTP 全部 mock）。

背景：P主大家族模板改为先查 voca.wiki 的 `Category:P主模板`（含模板重定向）建成的字典，
命中就直接用，字典里没有的才回退到逐个搜索。
"""
import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from utils import voca


def _payload():
    """模拟 generator=categorymembers + prop=redirects 的返回。"""
    return {"query": {"pages": [
        {"title": "Template:40mP"},
        {"title": "Template:Chinozo"},
        {"title": "Template:Hachi", "redirects": [{"title": "Template:米津玄師"},
                                                   {"title": "Template:米津玄师"}]},
        {"title": "Template:Livetune", "redirects": [{"title": "Template:Kz"}]},
        {"title": "Template:被删的模板", "missing": True},
    ]}}


class _Session:
    """按顺序返回若干 payload 的假会话。"""

    def __init__(self, *payloads):
        self.payloads = list(payloads) or [_payload()]
        self.calls = 0
        self.params = []

    def get(self, url, params=None, timeout=None):
        self.params.append(dict(params or {}))
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return mock.Mock(json=lambda: payload)


def _person(name, name_eng=()):
    return SimpleNamespace(name=name, name_eng=list(name_eng))


class ProducerTemplateDictTest(TestCase):
    def setUp(self):
        voca._producer_template_cache = None
        voca._producer_template_index = None

    def tearDown(self):
        voca._producer_template_cache = None
        voca._producer_template_index = None

    def _fetch(self, session=None):
        session = session or _Session()
        with mock.patch.object(voca.login, "get_api_session", return_value=session), \
             mock.patch.object(voca.login, "api_url", return_value="https://voca.wiki/api.php"):
            return voca.fetch_producer_templates(), session

    def test_builds_dict_from_category_and_redirects(self):
        mapping, session = self._fetch()
        self.assertEqual("Hachi", mapping["Hachi"])          # 模板本身
        self.assertEqual("Hachi", mapping["米津玄師"])        # 重定向 → 真模板
        self.assertEqual("Hachi", mapping["米津玄师"])
        self.assertEqual("Livetune", mapping["Kz"])
        self.assertNotIn("被删的模板", mapping)               # missing 的跳过
        self.assertEqual(1, session.calls)                   # 分类成员 + 重定向一次拿全
        self.assertEqual(voca.PRODUCER_TEMPLATE_CATEGORY,
                         session.params[0]["gcmtitle"])
        self.assertEqual("max", session.params[0]["rdlimit"])

    def test_result_is_cached_until_refresh(self):
        proc, session = self._fetch()
        self.assertEqual(7, len(proc))                       # 4 个模板 + 3 个重定向
        with mock.patch.object(voca.login, "get_api_session", return_value=session), \
             mock.patch.object(voca.login, "api_url", return_value="https://voca.wiki/api.php"):
            voca.fetch_producer_templates()                  # 走缓存
            self.assertEqual(1, session.calls)
            voca.fetch_producer_templates(refresh=True)      # 强制重取
            self.assertEqual(2, session.calls)

    def test_paginates_until_no_continue(self):
        session = _Session(
            {"query": {"pages": [{"title": "Template:A"}]},
             "continue": {"gcmcontinue": "next", "continue": "-||"}},
            {"query": {"pages": [{"title": "Template:B"}]}},
        )
        mapping, session = self._fetch(session)
        self.assertEqual(["A", "B"], sorted(mapping))
        self.assertEqual(2, session.calls)
        self.assertEqual("next", session.params[1]["gcmcontinue"])

    def test_network_failure_returns_empty_dict(self):
        session = mock.Mock()
        session.get.side_effect = OSError("boom")
        with mock.patch.object(voca.login, "get_api_session", return_value=session), \
             mock.patch.object(voca.login, "api_url", return_value="https://voca.wiki/api.php"):
            self.assertEqual({}, voca.fetch_producer_templates())
            self.assertIsNone(voca.lookup_producer_template("40mP"))

    def test_lookup_ignores_case_underscore_and_spaces(self):
        self._fetch()
        self.assertEqual("40mP", voca.lookup_producer_template("40mP"))
        self.assertEqual("40mP", voca.lookup_producer_template("40mp"))
        self.assertEqual("Hachi", voca.lookup_producer_template("米津玄師"))
        self.assertEqual("Livetune", voca.lookup_producer_template("kz"))
        self.assertIsNone(voca.lookup_producer_template("不在wiki里的人"))
        self.assertIsNone(voca.lookup_producer_template(""))


class GetProducerTemplatesTest(TestCase):
    """`get_producer_templates`：命中字典就不搜索，没命中的才搜索。"""

    DICT = {"40mP": "40mP", "Hachi": "Hachi", "米津玄師": "Hachi", "Livetune": "Livetune",
            "Kz": "Livetune"}

    def _get(self, producers, checker):
        with mock.patch.object(voca, "producer_checker", checker), \
             mock.patch.object(voca, "fetch_producer_templates", return_value=self.DICT):
            return asyncio.run(voca.get_producer_templates(producers))

    def test_dict_hit_skips_search(self):
        checker = mock.AsyncMock(return_value=[])
        self.assertEqual(["40mP", "Hachi"],
                         self._get([_person("40mP"), _person("米津玄師")], checker))
        checker.assert_not_called()

    def test_english_name_and_trailing_p_variants(self):
        checker = mock.AsyncMock(return_value=[])
        self.assertEqual(["Hachi"], self._get([_person("Hachi", name_eng=["ハチ"])], checker))
        self.assertEqual(["40mP"], self._get([_person("40m", name_eng=["40mP"])], checker))
        checker.assert_not_called()

    def test_unknown_producer_falls_back_to_search(self):
        checker = mock.AsyncMock(return_value=["某人P"])
        result = self._get([_person("40mP"), _person("某人P")], checker)
        self.assertEqual(["40mP", "某人P"], result)
        checker.assert_called_once()
        # 只把没命中的那个交给搜索
        self.assertEqual(["某人P"], [p.name for p in checker.call_args.args[0]])

    def test_results_are_deduped(self):
        checker = mock.AsyncMock(return_value=["40mP"])
        self.assertEqual(["40mP"], self._get([_person("40mP"), _person("X")], checker))

    def test_search_failure_keeps_dict_hits(self):
        # producer_checker 自己吞异常并返回空列表，字典命中不受影响
        checker = mock.AsyncMock(return_value=[])
        self.assertEqual(["Hachi"],
                         self._get([_person("米津玄師"), _person("炸了的P")], checker))
        checker.assert_called_once()
