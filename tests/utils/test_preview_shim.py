"""预览文档的「小补丁」：把 `{{bilibiliVideo}}` 的播放器挂进预览（`utils/preview_shim.py`）。

背景（用户 2026-10-22 报）：站上的模板把播放器交给 `Widget:BilibiliVideo` 的脚本去挂，
那段脚本依赖 jQuery / `window.RLQ` / `mw.config`，预览文档里没有这些 →
`<div class="bilibili-video-container">` 保持 Widget CSS 给的 `display: none`，
预览里那块地方看着像「模板没生效」。
"""
from unittest import TestCase

from utils import preview_shim


class MountEmbeddedPlayersTest(TestCase):
    DOC = ("<!DOCTYPE html><html><head></head><body>"
           '<div class="bilibili-video-container" data-id="BV1oh411L797"></div>'
           "</body></html>")

    def test_script_goes_right_before_the_body_end(self):
        document = preview_shim.mount_embedded_players(self.DOC)
        self.assertIn("<script>", document)
        self.assertLess(document.rindex("<script>"), document.index("</body>"))
        self.assertIn('</script></body></html>', document)

    def test_without_a_body_tag_the_script_is_appended(self):
        document = preview_shim.mount_embedded_players("<p>只有一段</p>")
        self.assertTrue(document.startswith("<p>只有一段</p>"))
        self.assertIn("<script>", document)

    def test_empty_document_stays_empty(self):
        self.assertEqual("", preview_shim.mount_embedded_players(""))

    def test_script_mounts_the_player_but_hides_the_sites_dead_toggles(self):
        script = preview_shim.mount_embedded_players(self.DOC)
        self.assertIn(".bilibili-video-container[data-id]", script)
        self.assertIn("player.bilibili.com/player.html", script)
        # 桌面预览的 QtWebEngine 没有 H.264 → 那种情况改成链接卡片
        self.assertIn("canPlayType", script)
        self.assertIn("在 bilibili 观看：", script)
        # 站上那两个开关（宽屏模式 / 显示视频）依赖它自己的脚本，预览里留着就是死按钮
        self.assertIn(".bilibili-toggle", script)

    def test_only_the_expected_ids_are_accepted(self):
        script = preview_shim.mount_embedded_players(self.DOC)
        # BV 号 / av 号 / 纯数字（aid）三种写法，其余一律跳过
        self.assertIn("^BV1[0-9A-Za-z]{9}$", script)
        self.assertIn(r"^av\d+$", script)

    def test_cards_are_embedded_for_the_no_h264_fallback(self):
        """没有 H.264 时（桌面预览）用封面画卡片：封面 / 标题是 Python 侧取好的。"""
        cards = {"BV1oh411L797": {"id": "BV1oh411L797", "title": "标题</script>",
                                  "owner": "UP主", "pic": "https://i0.hdslb.com/a.jpg",
                                  "url": "https://www.bilibili.com/video/BV1oh411L797"}}
        script = preview_shim.mount_embedded_players(self.DOC, cards)
        self.assertIn("var VOCAWIKI_BILIBILI = ", script)
        self.assertIn("https://i0.hdslb.com/a.jpg", script)
        self.assertIn("posterCard", script)
        self.assertIn("点封面去 bilibili 看", script)
        # 内容里的 `</script>` 不能提前结束脚本
        self.assertNotIn("标题</script>", script)
        self.assertIn("标题<\\/script>", script)

    def test_no_cards_means_the_plain_link_card(self):
        script = preview_shim.mount_embedded_players(self.DOC)
        self.assertIn("var VOCAWIKI_BILIBILI = {};", script)
        self.assertIn("在 bilibili 观看：", script)
