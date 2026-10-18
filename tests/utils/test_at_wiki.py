import os
from unittest import TestCase
from unittest import mock

# ⚠️ 跑测时**绝不能**真去开浏览器取数（AtWiki 被 Cloudflare 挑战时 `fetch_page_html()`
# 会退回 `utils/browser_fetch.py`）——一开就是十几秒，还会在屏幕上闪一个窗口。
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"

from utils import at_wiki


def _response(html: str, status: int = 200) -> mock.Mock:
    """`http_get` 的假响应：直接请求成功（200 + 正常页面）。"""
    response = mock.Mock()
    response.status_code = status
    response.text = html
    return response


class VocaloidCollectionInfoTest(TestCase):
    @mock.patch.object(at_wiki, "find_at_wiki_page", return_value="https://example.test/song")
    @mock.patch.object(at_wiki, "http_get")
    def test_returns_top30_track_and_rank(self, mock_http_get, _mock_find_page):
        response = mock_http_get.return_value
        response.text = "ボカコレ2020冬 TOP30ランキング第30位"

        self.assertEqual(
            ("ボカコレ2020冬", "TOP30", "30"),
            at_wiki.get_vocaloid_collection_info("ネリネージュ"))
        response.raise_for_status.assert_called_once_with()


# 真实的 vocaloidchly 页面结构（`#wikibody` 里是表头 + 正文，末尾有站内搜索链接与「最終更新」块）
PAGE_HTML = """
<html><head><title>日蝕 - vocaloid中文歌詞wiki - atwiki（アットウィキ）</title></head>
<body><div id="wikibody">作詞：すんｚりヴぇｒ
歌：鏡音リン・鏡音レン

翻譯：夜明晴月

日蝕

想一眼就看穿那橙色之物吧?
這點我跟你真是一丘之貉呢
<div class="atwiki-page-keyword">
<a href="//w.atwiki.jp/vocaloidchly/search?keyword=x">「日蝕」をウィキ内検索</a>
</div>
<div class="atwiki-lastmodify">最終更新：2026年10月15日</div>
</div></body></html>
"""


class PageLyricsTest(TestCase):
    """歌词页「从链接填充」按**页面地址**取歌词（不搜索）。"""

    def test_parse_page_reads_translator_and_body(self):
        lyrics = at_wiki.parse_page(PAGE_HTML)
        self.assertEqual("VOCALOID中文歌词wiki", lyrics.source_name)
        self.assertEqual("夜明晴月", lyrics.translator)
        self.assertIn(("翻譯", "夜明晴月"), lyrics.staff)
        self.assertIn("想一眼就看穿那橙色之物吧?", lyrics.lyrics_chs)
        self.assertNotIn("最終更新", lyrics.lyrics_chs, "「最終更新」那行要丢掉")
        self.assertNotIn("ウィキ内検索", lyrics.lyrics_chs,
                         "页面末尾的站内搜索链接不是歌词，要丢掉（否则「只有中文」会判成中日混杂）")

    def test_parse_body_drops_a_wiki_search_line(self):
        """就算正文里只剩这一行（HTML 结构已经拿不到时），也不能当成歌词。"""
        self.assertEqual("你的名字",
                         at_wiki.parse_at_wiki_body("你的名字\n「歌名」をウィキ内検索", "歌名"))

    def test_parse_page_drops_the_title_line_of_the_page_body(self):
        """正文第一行是**歌名**（页面标题）时不算歌词（真机：日蝕 / 蓬萊花 / 旋律王姫-…）。"""
        self.assertNotIn("日蝕", at_wiki.parse_page(PAGE_HTML).lyrics_chs)
        # 标题带括号 / 空白也一样认（比较用的键去掉了空白与括号）
        bracket = PAGE_HTML.replace("<title>日蝕 -", "<title>「日蝕」 -").replace(
            "翻譯：夜明晴月\n\n日蝕\n", "翻譯：夜明晴月\n\n「日蝕」\n")
        self.assertNotIn("日蝕", at_wiki.parse_page(bracket).lyrics_chs)
        # 只处理开头那一行：歌词里出现的歌名照旧保留（`parse_at_wiki_body` 自己会把
        # 单个空行压掉，那是它原本的分段口径，与这里无关）
        self.assertEqual("日蝕の歌を歌う\n日蝕",
                         at_wiki.parse_at_wiki_body("日蝕\n\n日蝕の歌を歌う\n日蝕", "日蝕"))
        # 不知道标题时按老样子（第一行照留）
        self.assertEqual("日蝕\n日蝕の歌を歌う",
                         at_wiki.parse_at_wiki_body("日蝕\n\n日蝕の歌を歌う"))
        nameless = PAGE_HTML.replace(
            "<title>日蝕 - vocaloid中文歌詞wiki - atwiki（アットウィキ）</title>",
            "<title>x</title>")
        self.assertIn("日蝕", at_wiki.parse_page(nameless).lyrics_chs)

    def test_parse_page_without_translator_leaves_it_blank(self):
        lyrics = at_wiki.parse_page(PAGE_HTML.replace("翻譯：夜明晴月\n", ""))
        self.assertEqual("", lyrics.translator, "没写翻译者就是空（不塞「ERROR!」，那是生成流程的事）")

    def test_parse_page_without_wikibody_returns_none(self):
        self.assertIsNone(at_wiki.parse_page("<html><body>nothing</body></html>"))

    @mock.patch.object(at_wiki, "http_get")
    def test_get_page_lyrics_fetches_the_given_url(self, mock_http_get):
        mock_http_get.return_value = _response(PAGE_HTML)
        lyrics = at_wiki.get_page_lyrics(
            "https://w.atwiki.jp/vocaloidchly/pages/5966.html")
        self.assertEqual("夜明晴月", lyrics.translator)
        self.assertEqual("https://w.atwiki.jp/vocaloidchly/pages/5966.html", lyrics.source_url)
        self.assertTrue(mock_http_get.call_args.kwargs["use_proxy"])

    @mock.patch.object(at_wiki.browser_fetch, "fetch_html", return_value=PAGE_HTML)
    @mock.patch.object(at_wiki, "http_get")
    def test_cloudflare_challenge_falls_back_to_the_browser(self, mock_http_get, fetch_html):
        """AtWiki 现在也挂 Cloudflare：直接请求拿到挑战页时借真浏览器取（2026-10-16 实测）。"""
        mock_http_get.return_value = _response(
            "<html><title>Just a moment...</title></html>", status=403)
        lyrics = at_wiki.get_page_lyrics("https://w.atwiki.jp/vocaloidchly/pages/5966.html")
        fetch_html.assert_called_once()
        self.assertEqual("夜明晴月", lyrics.translator)

    @mock.patch.object(at_wiki.browser_fetch, "fetch_html", return_value=None)
    @mock.patch.object(at_wiki, "http_get")
    def test_browser_failure_returns_none(self, mock_http_get, _fetch_html):
        """两条路都取不到时返回 None（调用方按「读不到这个页面」提示，不抛异常）。"""
        mock_http_get.return_value = _response("Just a moment", status=403)
        self.assertIsNone(
            at_wiki.get_page_lyrics("https://w.atwiki.jp/vocaloidchly/pages/5966.html"))

    @mock.patch.object(at_wiki, "find_at_wiki_page",
                       return_value="https://w.atwiki.jp/vocaloidchly/pages/5966.html")
    @mock.patch.object(at_wiki, "http_get")
    def test_generated_page_marks_a_missing_translator(self, mock_http_get, _find):
        """生成流程仍按老口径：页面没写「翻譯」时翻译者写「ERROR!」（显眼，用户好发现）。"""
        mock_http_get.return_value = _response(PAGE_HTML.replace("翻譯：夜明晴月\n", ""))
        lyrics = at_wiki.get_at_wiki_body("日蝕", ["https://example.test/search"], "Chinese", "")
        self.assertEqual("ERROR!", lyrics.translator)
