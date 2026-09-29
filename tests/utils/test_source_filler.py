"""utils/source_filler.py 的测试（不联网，HTTP 全部 mock）。

背景：歌词整理窗口「从链接填充」按来源链接识别翻译者 / 翻译链接 / 来源。
实测口径：网易云 = 贡献翻译者（其次贡献歌词者）；b 站视频 = 视频投稿者；b 站图文笔记 = 笔记撰写者。
"""
from unittest import TestCase
from unittest import mock

from utils import source_filler

NETEASE_PAYLOAD = {
    "code": 200,
    "transUser": {"id": 74328776, "userid": 105344906, "nickname": "白夜落星"},
    "lyricUser": {"id": 73261223, "userid": 5197952419, "nickname": "重叠广州"},
}
BILIBILI_PAYLOAD = {"code": 0, "data": {"owner": {"mid": 183675426, "name": "青杉折扇"}}}
COMMENT_URL = ("https://www.bilibili.com/video/BV15x411S7d5?comment_on=1"
               "&comment_root_id=2479540599&share_tag=s_i#reply2479540599")
# 真实的 opus 页面结构：作者卡片 + SSR JSON 里 name/mid 成对出现的作者对象
OPUS_HTML = (
    '<div class="opus-module-author"><div class="opus-module-author__name" style="color:;">'
    'kmrsc_</div></div>'
    '"name":"kmrsc_","name_render":null,"label":"","mid":3493120362678931,'
    '"jump_url":"\\u002F\\u002Fspace.bilibili.com\\u002F3493120362678931"')
OPUS_URL = "https://www.bilibili.com/opus/1251644081024532483?spm_id_from=333.1387.0.0"
# 真实的巴哈姆特創作大廳作者卡片结构
BAHAMUT_URL = "https://home.gamer.com.tw/artwork.php?sn=6402210"
BAHAMUT_HTML = (
    '<div class="article-content head row"><div class="user-info-box">'
    '<a href="https://home.gamer.com.tw/q23074285" class="user-avatar-img gamercard" '
    'data-gamercard-userid="q23074285"><img src="https://avatar2.bahamut.com.tw/x.png"></a>'
    '<div class="info-text">'
    '<a href="https://home.gamer.com.tw/q23074285" class="caption-text primary">TYPE</a>'
    '<a href="https://home.gamer.com.tw/q23074285" class="caption-text">q23074285</a>'
    '</div></div>')


class ParseNeteaseTest(TestCase):
    def test_accepts_common_link_forms(self):
        for url in ("https://music.163.com/#/song?id=2637441995",
                    "https://music.163.com/song?id=2637441995",
                    "https://y.music.163.com/m/song?id=2637441995&userid=1"):
            self.assertEqual("2637441995", source_filler.parse_netease_song(url), url)

    def test_ignores_other_sites(self):
        self.assertIsNone(source_filler.parse_netease_song(
            "https://www.bilibili.com/video/BV1pj421Q7V3"))
        self.assertIsNone(source_filler.parse_netease_song(""))
        self.assertIsNone(source_filler.parse_netease_song("https://music.163.com/song"))


class ParseBilibiliTest(TestCase):
    def test_video_bv_and_av(self):
        self.assertEqual("BV1pj421Q7V3", source_filler.parse_bilibili_video(
            "https://www.bilibili.com/video/BV1pj421Q7V3/?p=1&t=3"))
        self.assertEqual("BV17x411w7KC", source_filler.parse_bilibili_video(
            "https://bilibili.com/video/av170001"))
        self.assertIsNone(source_filler.parse_bilibili_video("https://music.163.com/#/song?id=1"))

    def test_note_forms(self):
        self.assertEqual("888123", source_filler.parse_bilibili_note(
            "https://www.bilibili.com/opus/888123"))
        self.assertEqual("888123", source_filler.parse_bilibili_note(
            "https://www.bilibili.com/video/BV1pj421Q7V3?note_id=888123"))
        self.assertEqual("cv1234", source_filler.parse_bilibili_note(
            "https://www.bilibili.com/read/cv1234"))
        self.assertIsNone(source_filler.parse_bilibili_note(
            "https://www.bilibili.com/video/BV1pj421Q7V3"))

    def test_note_forms_for_dynamic_and_mobile(self):
        """动态（`t.bilibili.com/{id}`、`/dynamic/{id}`）与移动端专栏（`/read/mobile?id=`）。"""
        self.assertEqual("383180979106555325", source_filler.parse_bilibili_note(
            "https://t.bilibili.com/383180979106555325?tab=2"))
        self.assertEqual("888123", source_filler.parse_bilibili_note(
            "https://www.bilibili.com/dynamic/888123"))
        self.assertEqual("cv12345678", source_filler.parse_bilibili_note(
            "https://www.bilibili.com/read/mobile?id=12345678"))

    def test_note_is_dynamic(self):
        self.assertTrue(source_filler.bilibili_note_is_dynamic(
            "https://t.bilibili.com/383180979106555325"))
        self.assertTrue(source_filler.bilibili_note_is_dynamic(
            "https://www.bilibili.com/dynamic/888123"))
        self.assertFalse(source_filler.bilibili_note_is_dynamic(
            "https://www.bilibili.com/opus/1012509995710808065"))
        self.assertFalse(source_filler.bilibili_note_is_dynamic(
            "https://www.bilibili.com/read/cv24055465"))

    def test_note_url(self):
        self.assertEqual("https://www.bilibili.com/read/cv24055465",
                         source_filler.bilibili_note_url("cv24055465"))
        self.assertEqual("https://www.bilibili.com/opus/1012509995710808065",
                         source_filler.bilibili_note_url("1012509995710808065"))

    def test_short_link_forms(self):
        self.assertTrue(source_filler.SHORT_LINK_RE.match("https://b23.tv/abcd123"))
        self.assertTrue(source_filler.SHORT_LINK_RE.match("https://bili2233.cn/abcd123"))
        self.assertIsNone(source_filler.SHORT_LINK_RE.match(
            "https://www.bilibili.com/video/BV1pj421Q7V3"))


class ParseCommentTest(TestCase):
    def test_parses_bv_and_comment_ids(self):
        self.assertEqual(("BV15x411S7d5", "2479540599", "2479540599"),
                         source_filler.parse_bilibili_comment(COMMENT_URL))

    def test_sub_reply_keeps_both_ids(self):
        url = "https://www.bilibili.com/video/BV15x411S7d5?comment_root_id=111#reply222"
        self.assertEqual(("BV15x411S7d5", "111", "222"),
                         source_filler.parse_bilibili_comment(url))

    def test_root_only_link(self):
        url = "https://www.bilibili.com/video/BV15x411S7d5?comment_root_id=111"
        self.assertEqual(("BV15x411S7d5", "111", "111"),
                         source_filler.parse_bilibili_comment(url))

    def test_secondary_id_is_used_as_reply(self):
        """App 有时只给 `comment_secondary_id` 而不给 `#reply`（子评论 → 别把根评论作者填进去）。"""
        url = ("https://www.bilibili.com/video/BV15x411S7d5?comment_on=1"
               "&comment_root_id=111&comment_secondary_id=222")
        self.assertEqual(("BV15x411S7d5", "111", "222"),
                         source_filler.parse_bilibili_comment(url))

    def test_plain_video_link_is_not_a_comment(self):
        self.assertEqual(("", "", ""), source_filler.parse_bilibili_comment(
            "https://www.bilibili.com/video/BV15x411S7d5"))


class NeteaseUserTest(TestCase):
    def test_prefers_translation_contributor(self):
        self.assertEqual(("白夜落星", NETEASE_PAYLOAD["transUser"]),
                         source_filler.netease_translator(NETEASE_PAYLOAD))

    def test_falls_back_to_lyric_contributor(self):
        name, user = source_filler.netease_translator({"lyricUser": NETEASE_PAYLOAD["lyricUser"]})
        self.assertEqual("重叠广州", name)
        self.assertEqual(NETEASE_PAYLOAD["lyricUser"], user)

    def test_empty(self):
        self.assertEqual(("", {}), source_filler.netease_translator({}))
        self.assertEqual(("", {}), source_filler.netease_translator({"transUser": {"nickname": "  "}}))

    def test_user_url_uses_profile_id(self):
        self.assertEqual("https://music.163.com/#/user/home?id=105344906",
                         source_filler.netease_user_url(NETEASE_PAYLOAD["transUser"]))
        self.assertEqual("", source_filler.netease_user_url({}))


class BilibiliAuthorTest(TestCase):
    def test_owner(self):
        self.assertEqual(("青杉折扇", "183675426"),
                         source_filler.bilibili_owner(BILIBILI_PAYLOAD["data"]))
        self.assertEqual(("", ""), source_filler.bilibili_owner({}))

    def test_opus_page_author_from_card_and_json(self):
        # 卡片里的名字 + JSON 里同名的 mid（真实 1251644081024532483 页面的结构）
        self.assertEqual(("kmrsc_", "3493120362678931"),
                         source_filler.bilibili_page_author(OPUS_HTML))

    def test_opus_card_without_json_pair_uses_space_link(self):
        html = ('<div class="opus-module-author__name">某人</div>'
                '<a href="\\u002F\\u002Fspace.bilibili.com\\u002F123">')
        self.assertEqual(("某人", "123"), source_filler.bilibili_page_author(html))

    def test_page_author_from_initial_state(self):
        html = '<script>window.__INITIAL_STATE__={"module_author":{"mid":7,"name":"笔记作者"}};</script>'
        self.assertEqual(("笔记作者", "7"), source_filler.bilibili_page_author(html))

    def test_page_author_prefers_anchor_neighbourhood(self):
        # 正文里提到的别人不能当成作者
        html = ('{"comment":{"mid":1,"name":"路人"}},'
                '"module_author":{"mid":7,"name":"笔记作者"}')
        self.assertEqual(("笔记作者", "7"), source_filler.bilibili_page_author(html))

    def test_page_author_handles_string_mid(self):
        html = '"name":"某人","label":"","mid":"3493120362678931"'
        self.assertEqual(("某人", "3493120362678931"), source_filler.bilibili_page_author(html))

    def test_page_author_missing(self):
        self.assertEqual(("", ""), source_filler.bilibili_page_author(""))
        self.assertEqual(("", ""), source_filler.bilibili_page_author("<html>nothing</html>"))

    def test_comment_author_picks_shared_reply(self):
        payload = {"data": {
            "root": {"rpid": 111, "member": {"mid": 1, "uname": "根评论者"}},
            "replies": [{"rpid": 222, "member": {"mid": 2, "uname": "子评论者"}}]}}
        self.assertEqual(("子评论者", "2"),
                         source_filler.bilibili_comment_author(payload, "111", "222"))
        self.assertEqual(("根评论者", "1"),
                         source_filler.bilibili_comment_author(payload, "111", "111"))

    def test_comment_author_falls_back_to_root(self):
        payload = {"data": {"root": {"rpid": 111, "member": {"mid": 1, "uname": "根评论者"}},
                             "replies": None}}
        self.assertEqual(("根评论者", "1"),
                         source_filler.bilibili_comment_author(payload, "111", "999"))
        self.assertEqual(("", ""), source_filler.bilibili_comment_author({}, "111", "111"))

    def test_comment_by_id_does_not_fall_back(self):
        """按 id 找那条评论：找不到就 None（不能退回根评论，不然会静静填错人）。"""
        payload = {"data": {"root": {"rpid": 111, "member": {"mid": 1, "uname": "根评论者"}},
                             "replies": [{"rpid": 222, "member": {"mid": 2, "uname": "子评论者"}}]}}
        self.assertEqual(("子评论者", "2"), source_filler.bilibili_comment_by_id(payload, "222"))
        self.assertEqual(("根评论者", "1"), source_filler.bilibili_comment_by_id(payload, "111"))
        self.assertIsNone(source_filler.bilibili_comment_by_id(payload, "999"))
        self.assertIsNone(source_filler.bilibili_comment_by_id(payload, ""))


class ParseBahamutTest(TestCase):
    def test_artwork_link(self):
        self.assertEqual(("artwork.php", "6402210"),
                         source_filler.parse_bahamut_artwork(BAHAMUT_URL))

    def test_creation_detail_link(self):
        self.assertEqual(("creationDetail.php", "123456"),
                         source_filler.parse_bahamut_artwork(
                             "https://home.gamer.com.tw/creationDetail.php?sn=123456"))

    def test_ignores_other_links(self):
        self.assertIsNone(source_filler.parse_bahamut_artwork(
            "https://home.gamer.com.tw/index.php"))
        self.assertIsNone(source_filler.parse_bahamut_artwork(
            "https://www.bilibili.com/video/BV15x411S7d5"))
        self.assertIsNone(source_filler.parse_bahamut_artwork(""))


class BahamutAuthorTest(TestCase):
    def test_reads_nickname_and_account(self):
        self.assertEqual(("TYPE", "q23074285"), source_filler.bahamut_author(BAHAMUT_HTML))

    def test_title_fallback_uses_account(self):
        html = "<title>Verna – 記憶の音 - q23074285的創作 - 巴哈姆特</title>"
        self.assertEqual(("q23074285", "q23074285"), source_filler.bahamut_author(html))

    def test_card_without_nickname_uses_account(self):
        html = '<div class="user-info-box"><a data-gamercard-userid="abc123"></a></div>'
        self.assertEqual(("abc123", "abc123"), source_filler.bahamut_author(html))

    def test_missing(self):
        self.assertEqual(("", ""), source_filler.bahamut_author("<html>nothing</html>"))
        self.assertEqual(("", ""), source_filler.bahamut_author(""))


class FillSourceTest(TestCase):
    def _response(self, payload=None, text=""):
        response = mock.Mock()
        response.json.return_value = payload
        response.text = text
        response.raise_for_status.return_value = None
        return response

    def _patch(self, payload=None, text="", error=None):
        if error is not None:
            return mock.patch.object(source_filler, "http_get", side_effect=error)
        return mock.patch.object(source_filler, "http_get",
                                 return_value=self._response(payload, text))

    def test_netease_fills_translator_and_source(self):
        with self._patch(payload=NETEASE_PAYLOAD) as get:
            result = source_filler.fill_source("https://music.163.com/#/song?id=2637441995")
        self.assertTrue(result["ok"])
        self.assertEqual("白夜落星", result["translator"])
        self.assertEqual("https://music.163.com/#/user/home?id=105344906", result["translatorUrl"])
        self.assertEqual("网易云音乐", result["sourceName"])
        self.assertEqual("https://music.163.com/#/song?id=2637441995", result["sourceUrl"])
        self.assertIn("贡献翻译者", result["message"])
        self.assertIn("id=2637441995", get.call_args[0][0])

    def test_netease_falls_back_to_lyric_contributor(self):
        with self._patch(payload={"lyricUser": NETEASE_PAYLOAD["lyricUser"]}):
            result = source_filler.fill_source("https://music.163.com/song?id=1901371647")
        self.assertTrue(result["ok"])
        self.assertEqual("重叠广州", result["translator"])
        self.assertIn("贡献歌词者", result["message"])
        self.assertEqual("https://music.163.com/#/song?id=1901371647", result["sourceUrl"])

    def test_netease_without_contributors(self):
        with self._patch(payload={"code": 200}):
            result = source_filler.fill_source("https://music.163.com/#/song?id=1")
        self.assertFalse(result["ok"])
        self.assertIn("没有翻译者", result["error"])

    def test_bilibili_video_uses_uploader(self):
        with self._patch(payload=BILIBILI_PAYLOAD):
            result = source_filler.fill_source("https://www.bilibili.com/video/BV1pj421Q7V3/?p=1")
        self.assertTrue(result["ok"])
        self.assertEqual("青杉折扇", result["translator"])
        self.assertEqual("https://space.bilibili.com/183675426", result["translatorUrl"])
        self.assertEqual("bilibili", result["sourceName"])
        self.assertEqual("https://www.bilibili.com/video/BV1pj421Q7V3", result["sourceUrl"])
        self.assertIn("投稿者", result["message"])

    def test_bilibili_video_error_code(self):
        with self._patch(payload={"code": -404, "message": "啥都木有"}):
            result = source_filler.fill_source("https://www.bilibili.com/video/BV1pj421Q7V3")
        self.assertFalse(result["ok"])
        self.assertIn("-404", result["error"])

    def test_bilibili_note_uses_page_author(self):
        with self._patch(text=OPUS_HTML):
            result = source_filler.fill_source(OPUS_URL)
        self.assertTrue(result["ok"])
        self.assertEqual("kmrsc_", result["translator"])
        self.assertEqual("https://space.bilibili.com/3493120362678931", result["translatorUrl"])
        self.assertEqual("https://www.bilibili.com/opus/1251644081024532483", result["sourceUrl"])
        # 站内写法：专栏（`/opus/…` 与 `/read/cv…`）的来源名是「bilibili专栏」
        self.assertEqual("bilibili专栏", result["sourceName"])
        self.assertIn("专栏作者", result["message"])

    def test_bilibili_column_link_returns_after_the_shell_page(self):
        """旧专栏链接 `/read/cv…`：先走 mobile 路由（`/read/cv…` 会 301 到 3KB 的 JS 外壳页，读不到作者）。"""
        with self._patch(text=OPUS_HTML) as get:
            result = source_filler.fill_source("https://www.bilibili.com/read/cv24055465")
        self.assertTrue(result["ok"])
        self.assertEqual("bilibili专栏", result["sourceName"])
        # 写进 wikitext 的是规范的专栏地址，抓的作者信息却是从 mobile 路由来的（它 301 到 opus SSR 页）
        self.assertEqual("https://www.bilibili.com/read/cv24055465", result["sourceUrl"])
        self.assertEqual("https://www.bilibili.com/read/mobile?id=24055465", get.call_args[0][0])

    def test_bilibili_column_falls_back_to_the_slash_route(self):
        """mobile 路由也只给外壳页时，退到 `/read/cv…/` 再试（最多两次请求）。"""
        with self._patch(text="<html>nothing</html>") as get:
            result = source_filler.fill_source("https://www.bilibili.com/read/cv24055465")
        self.assertFalse(result["ok"])
        self.assertEqual(2, get.call_count)
        self.assertEqual("https://www.bilibili.com/read/cv24055465/", get.call_args_list[1][0][0])

    def test_bilibili_dynamic_uses_the_dynamic_source_name(self):
        """动态（`t.bilibili.com/{id}`）：来源名写「bilibili动态」（站内写法）。"""
        with self._patch(text=OPUS_HTML) as get:
            result = source_filler.fill_source("https://t.bilibili.com/383180979106555325?tab=2")
        self.assertTrue(result["ok"])
        self.assertEqual("bilibili动态", result["sourceName"])
        self.assertEqual("https://www.bilibili.com/opus/383180979106555325", result["sourceUrl"])
        self.assertIn("动态作者", result["message"])
        self.assertEqual("https://www.bilibili.com/opus/383180979106555325", get.call_args[0][0])

    def test_short_link_is_resolved_before_parsing(self):
        """App 分享出来的 b23.tv 短链：先跟一次重定向拿真链接，再按真链接填充。"""
        redirect = self._response(text="")
        redirect.url = "https://www.bilibili.com/opus/1012509995710808065?spm_id_from=333.0.0"
        page = self._response(text=OPUS_HTML)
        page.url = "https://www.bilibili.com/opus/1012509995710808065?spm_id_from=333.0.0"
        with mock.patch.object(source_filler, "http_get",
                               side_effect=[redirect, page]) as get:
            result = source_filler.fill_source("https://b23.tv/abc123")
        self.assertTrue(result["ok"])
        self.assertEqual("kmrsc_", result["translator"])
        self.assertEqual("https://b23.tv/abc123", get.call_args_list[0][0][0])
        self.assertIn("opus/1012509995710808065", get.call_args_list[1][0][0])

    def test_short_link_that_cannot_be_resolved_still_reports_unknown(self):
        """短链解析不出来时原样返回，最后由「认不出这个链接」报错（不要抛异常）。"""
        with mock.patch.object(source_filler, "http_get",
                               side_effect=RuntimeError("network down")):
            result = source_filler.fill_source("https://b23.tv/abc123")
        self.assertFalse(result["ok"])
        self.assertIn("认不出", result["error"])

    def test_bilibili_comment_sub_reply_on_a_later_page(self):
        """分享的子评论不在地一页（一页 20 条）→ 按 pn 往后翻，别拿根评论的作者充数。"""
        view = {"code": 0, "data": {"aid": 10217353}}

        def reply_page(rpid, uname, mid):
            return {"code": 0, "data": {
                "root": {"rpid": 111, "member": {"mid": 1, "uname": "根评论者"}},
                "replies": [{"rpid": rpid, "member": {"mid": mid, "uname": uname}}]}}

        first = reply_page(222, "其他人", 2)
        second = reply_page(333, "子评论者", 3)
        url = ("https://www.bilibili.com/video/BV15x411S7d5?comment_root_id=111"
               "&comment_secondary_id=333")
        with mock.patch.object(source_filler, "http_get",
                               side_effect=[self._response(payload=view),
                                            self._response(payload=first),
                                            self._response(payload=second)]) as get:
            result = source_filler.fill_source(url)
        self.assertTrue(result["ok"])
        self.assertEqual("子评论者", result["translator"])
        self.assertEqual("https://space.bilibili.com/3", result["translatorUrl"])
        self.assertNotIn("没翻到", result["message"])
        self.assertIn("pn=1", get.call_args_list[1][0][0])
        self.assertIn("pn=2", get.call_args_list[2][0][0])

    def test_bilibili_comment_falls_back_to_root_with_a_note(self):
        """翻了几页都没找到分享的那条子评论 → 用根评论的作者，并在提示里说明。"""
        view = {"code": 0, "data": {"aid": 10217353}}
        page = {"code": 0, "data": {
            "root": {"rpid": 111, "member": {"mid": 1, "uname": "根评论者"}},
            "replies": [{"rpid": 222, "member": {"mid": 2, "uname": "其他人"}}]}}
        url = ("https://www.bilibili.com/video/BV15x411S7d5?comment_root_id=111"
               "&comment_secondary_id=999")
        with mock.patch.object(source_filler, "http_get",
                               side_effect=[self._response(payload=view)]
                               + [self._response(payload=page)] * source_filler.BILIBILI_REPLY_PAGES):
            result = source_filler.fill_source(url)
        self.assertTrue(result["ok"])
        self.assertEqual("根评论者", result["translator"])
        self.assertIn("没翻到", result["message"])

    def test_bilibili_comment_uses_commenter(self):
        view = {"code": 0, "data": {"aid": 10217353, "owner": {"mid": 1, "name": "投稿者"}}}
        reply = {"code": 0, "data": {"root": {"rpid": 2479540599,
                                             "member": {"mid": 390945978, "uname": "尘遗岁月"}},
                                     "replies": []}}
        with mock.patch.object(source_filler, "http_get",
                               side_effect=[self._response(payload=view),
                                            self._response(payload=reply)]) as get:
            result = source_filler.fill_source(COMMENT_URL)
        self.assertTrue(result["ok"])
        self.assertEqual("尘遗岁月", result["translator"])
        self.assertEqual("https://space.bilibili.com/390945978", result["translatorUrl"])
        self.assertEqual("bilibili视频评论区", result["sourceName"])
        self.assertEqual("https://www.bilibili.com/video/BV15x411S7d5?comment_on=1"
                         "&comment_root_id=2479540599#reply2479540599", result["sourceUrl"])
        self.assertIn("评论者", result["message"])
        self.assertIn("view?bvid=BV15x411S7d5", get.call_args_list[0][0][0])
        self.assertIn("reply/reply?type=1&oid=10217353&root=2479540599",
                      get.call_args_list[1][0][0])

    def test_bilibili_comment_missing(self):
        view = {"code": 0, "data": {"aid": 1}}
        reply = {"code": 0, "data": {"root": None, "replies": []}}
        with mock.patch.object(source_filler, "http_get",
                               side_effect=[self._response(payload=view),
                                            self._response(payload=reply)]):
            result = source_filler.fill_source(COMMENT_URL)
        self.assertFalse(result["ok"])
        self.assertIn("手动填写", result["error"])

    def test_bilibili_note_without_author(self):
        with self._patch(text="<html>nothing</html>"):
            result = source_filler.fill_source("https://www.bilibili.com/opus/888123456")
        self.assertFalse(result["ok"])
        self.assertIn("手动填写", result["error"])

    def test_unknown_link_mentions_bilibili_columns(self):
        result = source_filler.fill_source("https://example.com/song/1")
        self.assertFalse(result["ok"])
        self.assertIn("专栏", result["error"])
        self.assertIn("动态", result["error"])
    def test_bahamut_uses_creator(self):
        with self._patch(text=BAHAMUT_HTML) as get:
            result = source_filler.fill_source(BAHAMUT_URL)
        self.assertTrue(result["ok"])
        self.assertEqual("TYPE", result["translator"])
        self.assertEqual("https://home.gamer.com.tw/q23074285", result["translatorUrl"])
        self.assertEqual("巴哈姆特", result["sourceName"])
        self.assertEqual("https://home.gamer.com.tw/artwork.php?sn=6402210", result["sourceUrl"])
        self.assertIn("投稿者", result["message"])
        self.assertIn("artwork.php?sn=6402210", get.call_args[0][0])

    def test_bahamut_without_author(self):
        with self._patch(text="<html>nothing</html>"):
            result = source_filler.fill_source(BAHAMUT_URL)
        self.assertFalse(result["ok"])
        self.assertIn("手动填写", result["error"])

    def test_unknown_link(self):
        result = source_filler.fill_source("https://example.com/song/1")
        self.assertFalse(result["ok"])
        self.assertIn("认不出", result["error"])

    def test_empty_url(self):
        self.assertFalse(source_filler.fill_source("   ")["ok"])
        self.assertIn("请先填写", source_filler.fill_source("")["error"])

    def test_network_error_is_reported(self):
        with self._patch(error=RuntimeError("boom")):
            result = source_filler.fill_source("https://music.163.com/#/song?id=1")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])
