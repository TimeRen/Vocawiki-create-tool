"""提交页的「预览」：把 Vocawiki 渲染好的 HTML 交给浏览器看。

Android 上 `Intent.ACTION_VIEW` 打开 `file://` 会被 `FileUriExposedException` 挡掉
（API 24+ 不允许把 file:// 交给别的应用），所以以前点「预览」什么都不发生
（用户 2026-10-19 报「预览根本预览不了」）。这里改为起一个只监听 127.0.0.1 的小服务，
浏览器用 `http://127.0.0.1:<端口>/` 打开同一份文档。
"""
from __future__ import annotations

import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from utils import preview_shim

_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script\s*>", flags=re.IGNORECASE)
_VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'

FALLBACK_CSS = """
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       font-size: 15px; line-height: 1.75; color: #202122; padding: 14px 16px;
       word-break: break-word; }
img { max-width: 100%; height: auto; }
a { color: #3366cc; text-decoration: none; }
table { max-width: 100%; }
pre { overflow: auto; }
"""


def _escape(text: Any) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _insert_after_head_open(html: str, snippet: str) -> str:
    return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + snippet, html, count=1, flags=re.I)


def _insert_style(html: str, css: str, attribute: str = "") -> str:
    # 反斜杠不能写在 f-string 的 {} 里：那只有 Python 3.12+ 允许，Android 端是 3.11。
    safe_css = str(css).replace("</style", "<\\/style")
    style = f"<style {attribute}>{safe_css}</style>"
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return html + style


def build_document(result: Dict[str, Any], origin: str = "") -> str:
    """`action=parse` 的结果 → 一份能在手机浏览器里看的文档。

    `head` 是站点皮肤骨架（有的话直接用，图片 / 字号才像条目页）；外面那些
    `<script>` 一律去掉 —— 手机上不需要它们，也不该让 wikitext 带进来的脚本跑起来。
    """
    html = str(result.get("html") or "")
    head = _SCRIPT_RE.sub("", str(result.get("head") or ""))
    css = str(result.get("css") or "")
    content = ('<div id="content" class="mw-body" role="main">'
               '<div id="mw-content-text" class="mw-body-content">' + html + "</div></div>")
    base = f'<base href="{_escape(origin)}">' if origin else ""
    if "<html" in head.lower() and "<body" in head.lower():
        document = head
        if base and "<base" not in document.lower():
            document = _insert_after_head_open(document, base)
        if _VIEWPORT not in document:
            document = _insert_after_head_open(document, _VIEWPORT)
        if css:
            document = _insert_style(document, css)
    else:
        document = ('<!DOCTYPE html><html><head><meta charset="utf-8">' + _VIEWPORT +
                    (base or "") + "</head><body></body></html>")
        document = _insert_style(document, FALLBACK_CSS + css)
    body_close = re.search(r"</body\s*>", document, flags=re.IGNORECASE)
    if body_close:
        document = document[:body_close.start()] + content + document[body_close.start():]
    else:
        document += content
    if not document.lstrip().lower().startswith("<!doctype"):
        document = "<!DOCTYPE html>" + document
    return preview_shim.mount_embedded_players(document)


class _Handler(BaseHTTPRequestHandler):
    document = "<!DOCTYPE html><html><body>预览为空</body></html>"

    def _send(self, with_body: bool) -> None:
        body = self.document.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self) -> None:            # noqa: N802 - BaseHTTPRequestHandler 的约定
        self._send(True)

    def do_HEAD(self) -> None:           # noqa: N802
        self._send(False)

    def log_message(self, *_args) -> None:
        """别往 logcat 里刷每条请求。"""


_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


def serve(document: str) -> str:
    """把文档挂到本机回环地址上，返回可交给浏览器的 URL（服务只起一次，之后换内容）。"""
    global _server
    with _lock:
        _Handler.document = document or _Handler.document
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, name="preview-http",
                             daemon=True).start()
            logging.info("预览服务已启动：%s", _server.server_address)
        port = _server.server_address[1]
    return f"http://127.0.0.1:{port}/"
