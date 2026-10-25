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

# 真实的 chly《171》页面结构（pages/3236.html，2026-10-21 抓取）：歌词段落后一条
# `<hr />`，后面才是译者写的说明。中文栏原来把说明也当歌词。
RULED_PAGE_HTML = """
<html><head><title>171 - vocaloid中文歌詞wiki - atwiki（アットウィキ）</title></head>
<body><div id="wikibody" class="box">
<div>作詞：黒田亜津<br />歌：巡音ルカ、猫村いろは</div>
<div>翻譯：reiminato</div>
<div>
我們的面前　現在
<br />有著巨大的困難（敵人）
</div>
<div>
如果感到痛苦　就哭泣吧
<br />等到哭得累了
<br />請笑吧
</div>
<br />
<br />
<br />
<hr />
<br />
<div>
東北地方太平洋沖地震應援曲。
<br />萬分同感之下翻譯了。如果有任何錯誤請指正。
</div>
<div>
標題的171是「災害用伝言ダイヤル」的號碼。
<br />當日本發生重大災難時，可以使用電話播打171，留言告知自身安全。
</div>
<div class="atwiki-page-keyword">
<a href="//w.atwiki.jp/vocaloidchly/search?keyword=171">「171」をウィキ内検索</a>
</div>
<div class="atwiki-lastmodify">最終更新：2011年03月16日 22:36</div>
</div></body></html>
"""


class TranslationPrefetchTest(TestCase):
    """打开歌词页前静默查一次翻译页（用户 2026-10-20）。

    atwiki 对 `requests` 一律回 403 挑战页（2026-10-20 实测），所以两条路都要能退回
    `utils/browser_fetch.py`（那个窗口开在屏幕外，不会打扰用户）。
    """

    URL = "https://w.atwiki.jp/vocaloidchly/pages/3476.html"

    def test_plain_fetch_returns_the_parsed_lyrics(self):
        with mock.patch.object(at_wiki, "http_get", return_value=_response(PAGE_HTML)), \
             mock.patch.object(at_wiki.browser_fetch, "fetch_html") as browser:
            lyrics = at_wiki.get_page_lyrics_plain(self.URL)
        browser.assert_not_called()
        self.assertIsNotNone(lyrics)
        self.assertIn("想一眼就看穿那橙色之物吧?", lyrics.lyrics_chs)
        self.assertEqual("夜明晴月", lyrics.translator)
        self.assertEqual(self.URL, lyrics.source_url)

    def test_cloudflare_challenge_falls_back_to_the_browser(self):
        with mock.patch.object(at_wiki, "http_get",
                               return_value=_response("<html>Just a moment...</html>")), \
             mock.patch.object(at_wiki.browser_fetch, "fetch_html",
                               return_value=PAGE_HTML) as browser:
            lyrics = at_wiki.get_page_lyrics_plain(self.URL)
        browser.assert_called_once_with(self.URL)
        self.assertIsNotNone(lyrics)
        self.assertEqual("夜明晴月", lyrics.translator)

    def test_network_errors_return_none(self):
        with mock.patch.object(at_wiki, "http_get", side_effect=RuntimeError("boom")), \
             mock.patch.object(at_wiki.browser_fetch, "fetch_html", return_value=None):
            self.assertIsNone(at_wiki.get_page_lyrics_plain(self.URL))

    def test_a_page_for_another_song_with_the_same_name_is_not_used(self):
        """同名的另一首歌的翻译页：`page_matches_song()` 判否（用户 2026-10-25 报的《潘多拉》）。

        voca.wiki《潘多拉》是 buzzG 的《パンドラ》（sm39484089），而按歌名搜到的是
        やしきん 的《パンドラ》（sm10349470）—— 两页标题都只写「パンドラ」，
        但页面里嵌的稿件编号不同。
        """
        html = PAGE_HTML.replace(
            "</div>", '<a href="http://www.nicovideo.jp/watch/sm10349470"></a></div>', 1)
        with mock.patch.object(at_wiki, "http_get", return_value=_response(html)), \
             mock.patch.object(at_wiki.browser_fetch, "fetch_html") as browser:
            self.assertFalse(at_wiki.page_matches_song(html, ["sm39484089"]))
            self.assertTrue(at_wiki.page_matches_song(html, ["sm10349470"]),
                            "同一支稿件照常预填")
            lyrics = at_wiki.get_page_lyrics_plain(self.URL, "パンドラ")
        browser.assert_not_called()
        self.assertIsNotNone(lyrics, "解析这一步本身不看稿件编号（核对由调用方做）")

    def test_video_ids_are_read_from_the_page(self):
        html = ('<a href="http://www.nicovideo.jp/watch/sm10349470"></a>'
                '<iframe src="https://ext.nicovideo.jp/thumb/sm10349470"></iframe>'
                '<iframe src="https://www.youtube.com/embed/Ec97ggqPBFQ"></iframe>'
                '<a href="https://www.bilibili.com/video/BV1dXGB6eE78"></a>')
        self.assertEqual({"sm10349470", "ec97ggqpbfq", "bv1dxgb6ee78"},
                         at_wiki.video_ids_in_html(html))

    def test_pages_are_only_compared_per_video_site(self):
        """页面只嵌了 b 站转载、而我们只有 nico 编号时不判「不是这首歌」。"""
        page = '<a href="http://www.nicovideo.jp/watch/sm10349470"></a>'
        self.assertTrue(at_wiki.page_matches_song(page, ["Ec97ggqPBFQ"]))
        self.assertTrue(at_wiki.page_matches_song(page, []))
        self.assertTrue(at_wiki.page_matches_song("<html>nothing</html>", ["sm10349470"]))
        self.assertFalse(at_wiki.page_matches_song(page, ["sm39484089"]))

    def test_search_tries_the_producer_first(self):
        with mock.patch.object(at_wiki, "find_at_wiki_page", return_value="page") as find:
            self.assertEqual("page", at_wiki.find_translation_page("273.15", "ポンDEみっく"))
        name, urls, producer = find.call_args.args
        self.assertEqual(("273.15", "ポンDEみっく"), (name, producer))
        self.assertTrue(urls[0].startswith("https://w.atwiki.jp/vocaloidchly/list?keyword="),
                        "先按页面名搜（`/list`，不受 Cloudflare 挑战），再退到全文搜")
        self.assertTrue(any(url.startswith("https://w.atwiki.jp/vocaloidchly/search")
                            and "273.15+" in url for url in urls))
        self.assertFalse(find.call_args.kwargs["web_search"],
                         "顺手查一下不该再退到外网搜索（会多等一次请求）")

    def test_search_without_a_producer_uses_one_query(self):
        with mock.patch.object(at_wiki, "find_at_wiki_page", return_value=None) as find:
            self.assertIsNone(at_wiki.find_translation_page("273.15", ""))
        urls = find.call_args.args[1]
        self.assertEqual(2, len(urls), "一次按页面名搜 + 一次全文搜")
    def test_an_empty_name_is_not_searched(self):
        with mock.patch.object(at_wiki, "find_at_wiki_page") as find:
            self.assertIsNone(at_wiki.find_translation_page("  "))
        find.assert_not_called()

    def test_search_errors_are_swallowed(self):
        with mock.patch.object(at_wiki, "find_at_wiki_page", side_effect=RuntimeError("boom")):
            self.assertIsNone(at_wiki.find_translation_page("273.15"))


# `?cmd=search&keyword=171` 的真页面结构：`#wikibody` 里每个 `<li>` 一条，
# 链接是 `?cmd=word&…&pageid=N`，页面名在 `title` 上。
SEARCH_HTML = """<html><body><div id="wikibody" class="box">
<ul class="atwiki-list-card wiki_search_data">
  <li><a rel="nofollow" href="/vocaloidchly/?cmd=word&word=171&pageid=3236" title="171">171</a>
      <div class="atwiki-search-source">…災害用伝言ダイヤルの番号…</div></li>
  <li><a rel="nofollow" href="/vocaloidchly/?cmd=word&word=171&pageid=2700"
         title="猫村いろは">猫村いろは</a></li>
</ul></div></body></html>"""

# `/list?keyword=171` 的真页面结构：`table.pagelist` 里直接给 `/pages/N.html`，
# 页面名后面还带着 ` (5696d)` 这种尾巴。
LIST_HTML = """<html><body><div id="wikibody" class="box">
<table class="pagelist"><tr><td>ページ名</td></tr>
<tr><td><a href="//w.atwiki.jp/vocaloidchly/pages/3236.html">171 (5696d)</a></td></tr>
</table></div></body></html>"""


class AtWikiSearchTest(TestCase):
    """按歌名找翻译页：`/search`、`/list` 两种结果页都要认（2026-10-20）。"""

    URL = "https://w.atwiki.jp/vocaloidchly/pages/3236.html"

    def test_search_results_are_matched_by_page_name(self):
        with mock.patch.object(at_wiki, "http_get", return_value=_response(SEARCH_HTML)):
            found = at_wiki.find_at_wiki_page(
                "171", [at_wiki.CHLY_SEARCH_URL.format("171")], "ミズッP")
        self.assertEqual(self.URL, found, "页面名就是「171」→ 认出来")

    def test_page_name_search_results_lose_the_size_suffix(self):
        with mock.patch.object(at_wiki, "http_get", return_value=_response(LIST_HTML)):
            found = at_wiki.find_at_wiki_page(
                "171", [at_wiki.CHLY_LIST_URL.format("171")], "")
        self.assertEqual(self.URL, found, "`/list` 的 ` (5696d)` 尾巴要去掉再比")

    def test_a_challenged_search_falls_back_to_the_browser(self):
        with mock.patch.object(at_wiki, "http_get",
                               return_value=_response("Just a moment...")), \
             mock.patch.object(at_wiki.browser_fetch, "fetch_html",
                               return_value=SEARCH_HTML) as browser:
            found = at_wiki.find_at_wiki_page(
                "171", [at_wiki.CHLY_SEARCH_URL.format("171")], "ミズッP")
        browser.assert_called_once()
        self.assertEqual(self.URL, found)

    def test_another_song_is_not_matched(self):
        with mock.patch.object(at_wiki, "http_get", return_value=_response(SEARCH_HTML)):
            self.assertIsNone(at_wiki.find_at_wiki_page(
                "498", [at_wiki.CHLY_SEARCH_URL.format("498")], ""))


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

    def test_parse_page_drops_the_translators_note(self):
        """`翻譯：cyataku(不當之處敬請指正)` 的括号是自述，不是名字（用户 2026-10-25 报）。

        实测 w.atwiki.jp/vocaloidchly/pages/2231.html（やしきん《パンドラ》）的表头。
        """
        html = PAGE_HTML.replace("翻譯：夜明晴月", "翻譯：cyataku(不當之處敬請指正)")
        self.assertEqual("cyataku", at_wiki.parse_page(html).translator)

    def test_parse_body_drops_a_wiki_search_line(self):
        """就算正文里只剩这一行（HTML 结构已经拿不到时），也不能当成歌词。"""
        self.assertEqual("你的名字",
                         at_wiki.parse_at_wiki_body("你的名字\n「歌名」をウィキ内検索", "歌名"))

    def test_parse_body_drops_an_edit_note(self):
        """AtWiki 页尾的「12.22　事隔兩年餘修正」不是歌词（用户 2026-10-18 报）。"""
        self.assertEqual("最後一句",
                         at_wiki.parse_at_wiki_body(
                             "最後一句\n\n12.22\u3000事隔兩年餘修正", "Liquid Metal"))

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

    def test_parse_page_drops_a_title_block_that_stands_on_its_own(self):
        """歌名**单独成段**（译者把中文歌名单独写一行）时也不进歌词栏。

        真页面（chly pages/1266.html《13歳ノ殺人鬼》）：`翻譯：pumyau` 之后是单独一个
        `<div>13歲的殺人魔</div>`（前后各空好几个换行），再往下才是歌词 —— 以前
        「13歲的殺人魔」成了「中文」栏的第一句（用户 2026-10-22 报）。页面标题是日文，
        所以 `_title_like()` 认不出来，只能靠「独自成段」这个结构特征。
        """
        html = ('<html><head><title>13歳ノ殺人鬼 - vocaloid中文歌詞wiki - atwiki</title></head>'
                '<body><div id="wikibody">\n<div>\n翻譯：pumyau\n\n</div>\n<br>\n'
                "<div>\n13歲的殺人魔\n\n</div>\n<br>\n"
                "<div>\n為什麼？\n<br>\n救救我\n<br>\n好想被愛\n\n</div>\n</div></body></html>")
        lyrics = at_wiki.parse_page(html)
        self.assertEqual("為什麼？\n救救我\n好想被愛", lyrics.lyrics_chs)
        self.assertNotIn("13歲的殺人魔", lyrics.lyrics_chs)

    def test_parse_body_keeps_a_single_line_first_stanza(self):
        """第一句歌词自己成段（只隔一个空行）时不能当成歌名吃掉。"""
        body = at_wiki.parse_at_wiki_body("La la la\n\n下一句\nまた次の句", "歌名")
        self.assertTrue(body.startswith("La la la"), body)
        self.assertIn("また次の句", body)

    def test_parse_body_keeps_a_title_like_line_when_it_ends_with_punctuation(self):
        self.assertEqual("なぜ？\n\nそうなの",
                         at_wiki.parse_at_wiki_body("なぜ？\n\n\n\nそうなの", "歌名"))

    def test_parse_body_without_further_blocks_keeps_the_line(self):
        self.assertEqual("只有一句", at_wiki.parse_at_wiki_body("只有一句", "歌名"))

    def test_parse_page_drops_the_translator_note_before_the_lyrics(self):
        """译者声明 + 歌名都不算歌词（真机《273.15》：声明那行之后紧跟歌名，两者都进了中文栏）。

        页面标题是 `-273.15`、正文里写的是 `273.15`（少了负号），也要认出来。
        """
        html = PAGE_HTML.replace("<title>日蝕 -", "<title>-273.15 -").replace(
            "翻譯：夜明晴月\n\n日蝕\n",
            "翻譯：夜明晴月\n\n基於相互尊重，請取用翻譯者不要改動我的翻譯，感謝\n\n273.15\n\n")
        lyrics = at_wiki.parse_page(html)
        self.assertEqual("想一眼就看穿那橙色之物吧?\n這點我跟你真是一丘之貉呢", lyrics.lyrics_chs)
        self.assertNotIn("請取用", lyrics.lyrics_chs)
        self.assertNotIn("273.15", lyrics.lyrics_chs)

    def test_parse_body_skips_a_note_line_at_the_top(self):
        self.assertEqual("第一句\n第二句",
                         at_wiki.parse_at_wiki_body(
                             "感謝翻譯，請勿改動\n第一句\n第二句", "歌名"))

    def test_parse_page_drops_the_translators_notes_after_the_rule(self):
        """`<hr />` 之后的译者说明不是歌词（用户 2026-10-21 报 chly《171》）。

        真页面（pages/3236.html）：歌词段落到此为止，接着一条 `<hr />`，然后是
        「東北地方太平洋沖地震應援曲。…」「標題的171是…」这些说明 —— 它们原来整段进了
        「中文」栏。
        """
        lyrics = at_wiki.parse_page(RULED_PAGE_HTML)
        self.assertIn("如果感到痛苦　就哭泣吧", lyrics.lyrics_chs)
        self.assertNotIn("東北地方太平洋沖地震應援曲", lyrics.lyrics_chs)
        self.assertNotIn("災害用伝言ダイヤル", lyrics.lyrics_chs)

    def test_a_rule_before_the_lyrics_does_not_cut_the_page(self):
        """分割线要是放在正文前面（当装饰线用），不能把整页切空。"""
        html = ('<html><head><title>歌</title></head><body><div id="wikibody">'
                "<hr />"
                "<div>第一句<br />第二句<br />第三句<br />第四句</div>"
                "</div></body></html>")
        lyrics = at_wiki.parse_page(html)
        self.assertIn("第四句", lyrics.lyrics_chs)
        self.assertIn("第一句", lyrics.lyrics_chs)

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


# 初音ミク Wiki 的真页面结构（《咲うわ。》pages/56279，2026-10-21 取的）：
# 最前面是「目次」插件（`div.plugin_contents` 里列着 曲紹介 / 歌詞 / コメント），
# 之后才是资讯、曲紹介、歌詞、コメント。**目录里那几行**正是以前把整页截断的元凶。
HMIKU_HTML = """
<html><head><title>咲うわ。 - 初音ミク Wiki - atwiki（アットウィキ）</title></head>
<body><div id="wikibody" class="box">
<h2><a href="//w.atwiki.jp/hmiku/pages/56279.html">咲うわ。</a></h2>
<table class="atwiki_plugin_region"><tbody><tr>
<td class="atwiki_plugin_region_button"><span>+</span></td>
<td>目次</td>
<td><div class="plugin_contents"><ul>
<li><a href="#id_0962a0da">曲紹介</a></li>
<li><a href="#id_0a172479">歌詞</a></li>
<li><a href="#id_ca80e710">コメント</a></li></ul></div></td>
</tr></tbody></table>
<div>
作詞：きっと、ずっと、ぼっち。
<br />
作曲：きっと、ずっと、ぼっち。
<br />
唄：闇音レンリ
</div>
<h3 id="id_0962a0da">曲紹介</h3>
<div>曲名：『咲うわ。』(わらうわ。)</div>
<h3 id="id_0a172479">歌詞</h3>
<div>
（動画より書き起こし）

｢不完全｣、｢醜い｣、｢矜持(きょうじ)｣だ、ってガラクタ詰め込んだ

また今日もやったこんな事ばっか
</div>
<h3 id="id_ca80e710">コメント</h3>
<div>ほんとにこの曲好き...  -- 名無しさん</div>
</div></body></html>
"""


class HmikuPageTest(TestCase):
    """初音ミク Wiki（日文歌词的来源）的页面解析。

    用户 2026-10-21 报：《咲うわ。》明明有日文歌词，生成条目时却取不到。根因是页面最前面
    的「目次」里也写着 `コメント`，`parse_body()` 一看到就整页截断，`split_index` 变成 -1、
    `body` 只剩最后一个字（实测解析结果是「詞」）。
    """

    def test_the_table_of_contents_does_not_cut_the_page(self):
        lyrics = at_wiki.parse_page(HMIKU_HTML)
        self.assertEqual("｢不完全｣、｢醜い｣、｢矜持(きょうじ)｣だ、ってガラクタ詰め込んだ\n"
                         "また今日もやったこんな事ばっか", lyrics.lyrics_chs)

    def test_the_transcription_note_is_not_a_lyric_line(self):
        self.assertNotIn("書き起こし", at_wiki.parse_page(HMIKU_HTML).lyrics_chs)

    def test_the_comment_section_is_not_lyrics(self):
        self.assertNotIn("名無しさん", at_wiki.parse_page(HMIKU_HTML).lyrics_chs)

    def test_the_credits_are_read_from_the_japanese_page(self):
        lyrics = at_wiki.parse_page(HMIKU_HTML)
        self.assertIn(("作詞", "きっと、ずっと、ぼっち。"), lyrics.staff)
        self.assertNotIn("作曲", lyrics.lyrics_chs)

    def test_a_page_without_any_header_keeps_the_whole_body(self):
        """整页没有「：」时别去截头（以前 `divider` 会算成 -1，正文只剩最后一个字）。"""
        staff, body = at_wiki.parse_body("咲うわ。", "第一句\n第二句\n第三句")
        self.assertEqual([], staff)
        self.assertEqual("第一句\n第二句\n第三句", body)

    def test_japanese_lyrics_are_searched_on_the_hmiku_wiki(self):
        with mock.patch.object(at_wiki, "get_at_wiki_body", return_value=None) as body_get:
            at_wiki.get_japanese_lyrics("咲うわ。", "きっと、ずっと、ぼっち。")
        name, urls, lang, producer = body_get.call_args.args
        self.assertEqual(("咲うわ。", "Japanese", "きっと、ずっと、ぼっち。"),
                         (name, lang, producer))
        self.assertTrue(urls[0].startswith("https://w.atwiki.jp/hmiku/list?keyword="),
                        "先按页面名搜（`/list` 不会被 Cloudflare 挑战）")
        self.assertTrue(any(url.startswith("https://w.atwiki.jp/hmiku/search") for url in urls))
        self.assertFalse(any("vocaloidchly" in url for url in urls))

