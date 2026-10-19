"""提交页「预览」文档拼装（不需要 Kivy，纯字符串逻辑）。"""
import urllib.request
from unittest import TestCase

from mobile import preview

RESULT = {
    "html": "<p>正文</p>",
    "head": ('<!DOCTYPE html><html><head><meta charset="utf-8">'
             '<script src="https://evil.example/x.js"></script></head>'
             '<body class="mediawiki"></body></html>'),
    "css": "body{color:#123}",
}


class BuildDocumentTest(TestCase):
    def test_uses_the_site_skeleton_with_base_viewport_and_css(self):
        document = preview.build_document(RESULT, "https://voca.wiki")
        self.assertTrue(document.startswith("<!DOCTYPE html>"))
        self.assertIn('<base href="https://voca.wiki">', document)
        self.assertIn("width=device-width", document, "手机上要有 viewport，否则字小得看不清")
        self.assertIn("body{color:#123}", document)
        self.assertIn("<p>正文</p>", document, "渲染结果要塞进 body 里")
        self.assertLess(document.index("<p>正文</p>"), document.index("</body>"),
                        "内容要在 </body> 之前")

    def test_scripts_from_the_skin_are_dropped(self):
        """手机上不需要站点的 JS，也不该让 wikitext 带进来的脚本跑起来。"""
        document = preview.build_document(RESULT, "https://voca.wiki")
        self.assertNotIn("<script", document.lower())

    def test_falls_back_when_the_skin_is_missing(self):
        document = preview.build_document({"html": "<p>x</p>", "head": "", "css": ""},
                                          "https://voca.wiki")
        self.assertIn("<!DOCTYPE html>", document)
        self.assertIn("<p>x</p>", document)
        self.assertIn("width=device-width", document)

    def test_without_origin_there_is_no_base(self):
        document = preview.build_document({"html": "<p>x</p>", "head": "", "css": ""}, "")
        self.assertNotIn("<base", document)


class ServeTest(TestCase):
    def test_document_is_reachable_over_loopback_http(self):
        """Android 上 file:// 会被 FileUriExposedException 挡掉，所以走 127.0.0.1。"""
        url = preview.serve("<!DOCTYPE html><html><body><p>hi</p></body></html>")
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        self.assertIn("<p>hi</p>", body)

    def test_serving_twice_reuses_the_same_port(self):
        first = preview.serve("<html><body>1</body></html>")
        second = preview.serve("<html><body>2</body></html>")
        self.assertEqual(first, second)
        with urllib.request.urlopen(second, timeout=5) as response:
            self.assertIn("2", response.read().decode("utf-8"))
