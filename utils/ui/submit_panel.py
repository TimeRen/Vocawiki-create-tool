"""「提交」标签页：原 html/wikitext-editor.html 的 PyQt5 版。

左编辑框 / 右预览（QtWebEngine，照原来那套「headhtml 骨架 + 站点 CSS + 本地封面替换」拼文档），
预览走 utils/wiki_api.parse_wikitext（匿名可用），提交走 utils/submit_editor.SubmitApi
——两边都是纯 Python，和 html 版共用同一份实现。

原来窗口右下角那几张通知卡片改成状态栏 + 完成弹窗：提交成功给结果汇总与
「在浏览器中打开条目」，失败保持在界面上可直接改了重试。
"""
import json
import logging
import os
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import theme, widgets
from utils.ui.workers import FunctionWorker

# 预览用：把「尚未上传」的封面换成本地图片（Python 侧给不了 DOM，交给页面里的 JS 做）
PREVIEW_JS = """
(function (cover) {
  if (!cover || !cover.data || !cover.name) return false;
  function normalize(name) {
    return String(name == null ? '' : name).replace(/^File:/i, '')
      .replace(/_/g, ' ').trim().toLowerCase();
  }
  var wanted = normalize(cover.name);
  var replaced = false;
  Array.prototype.forEach.call(document.querySelectorAll('span[typeof~="mw:File"]'),
    function (span) {
      var link = span.querySelector('a[title]');
      if (!link) return;
      if (normalize(link.getAttribute('title')) !== wanted) return;
      var media = span.querySelector('[data-width]');
      var img = document.createElement('img');
      img.setAttribute('src', cover.data);
      img.setAttribute('alt', cover.name);
      if (media && media.getAttribute('data-width')) {
        img.setAttribute('width', media.getAttribute('data-width'));
      }
      span.parentNode.replaceChild(img, span);
      replaced = true;
    });
  Array.prototype.forEach.call(document.querySelectorAll('img[src]'), function (img) {
    var src = img.getAttribute('src') || '';
    if (src.indexOf('data:') === 0) return;
    var decoded;
    try { decoded = decodeURIComponent(src); } catch (e) { decoded = src; }
    if (normalize(decoded.replace(/^.*\\//, '')).indexOf(wanted) === -1 &&
        decoded.indexOf(encodeURIComponent(cover.name)) === -1) return;
    img.setAttribute('src', cover.data);
    replaced = true;
  });
  return replaced;
})(%s);
"""


def build_preview_html(result: Dict[str, Any], origin: str) -> str:
    """照 html 版的 buildDoc()：headhtml 是完整骨架，缺了才自己拼一份。"""
    html = result.get("html") or ""
    head = _strip_scripts(result.get("head") or "")
    css = result.get("css") or ""
    content = ('<div id="content" class="mw-body" role="main">'
               '<div id="mw-content-text" class="mw-body-content">' + html + "</div></div>")
    base = f'<base href="{_escape(origin or "")}">'
    if "<html" in head.lower() and "<body" in head.lower():
        if "<base" not in head.lower():
            head = _insert_after_head_open(head, base)
        document = head + content + "</body></html>"
    else:
        document = ('<!DOCTYPE html><html><head><meta charset="utf-8">' + base +
                    "</head><body class=\"mediawiki ltr sitedir-ltr ns-0 ns-subject action-view\">"
                    + content + "</body></html>")
        document = _insert_style(document, FALLBACK_CSS)
    if css:
        document = _insert_style(document, css, "data-vocawiki-site-css")
    if not document.lstrip().lower().startswith("<!doctype"):
        document = "<!DOCTYPE html>" + document
    return document


FALLBACK_CSS = """
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       font-size: 14px; line-height: 1.75; color: #202122; padding: 14px 18px; }
img { max-width: 100%; height: auto; }
a { color: #3366cc; text-decoration: none; }
table { max-width: 100%; }
pre { overflow: auto; }
"""


def error_doc(message: str) -> str:
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            'body{font-family:"Microsoft YaHei",sans-serif;font-size:13px;color:#b32424;'
            'padding:18px;line-height:1.7;word-break:break-word;}</style></head><body>'
            "<b>预览失败</b><br>" + _escape(message) + "</body></html>")


def _escape(text: Any) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _strip_scripts(text: str) -> str:
    import re
    return re.sub(r"<script[\s\S]*?</script>", "", text or "", flags=re.IGNORECASE)


def _insert_after_head_open(html: str, snippet: str) -> str:
    import re
    return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + snippet, html, count=1, flags=re.I)


def _insert_style(html: str, css: str, attribute: str = "") -> str:
    style = f"<style {attribute}>{str(css).replace('</style', '<\\/style')}</style>"
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return html + style


def backlink_page_text(item: Dict[str, Any]) -> str:
    """链入替换的一页结果 → 一行提示（✓ 标题（N 处 写法） / ✗ 标题：原因）。

    用户 2026-09-29 要求「成功提醒一个条目一个条目的冒」，所以每页各写一行。
    """
    title = str((item or {}).get("title") or "")
    if (item or {}).get("ok"):
        detail = f"{item.get('count')} 处"
        if item.get("kind"):
            detail += f"，{item['kind']}"
        return f"✓ {title}（{detail}）"
    return f"✗ {title}：{item.get('error') or '未改动'}"


class _PageProgress(QtCore.QObject):
    """逐页进度的转发器：工作线程里 `page.emit(item)`，主线程收到就交给 handler。

    链入替换是在 QThread 里跑的（`_run_background`），直接在那边碰控件不安全。
    ⚠️ 必须连到 **QObject 的方法**上（这里 `_deliver`）而不是普通函数 / lambda：
    Qt 只在「接收者是 QObject」时才按线程关系选队列连接，连到普通可调用对象时 PyQt
    按直接调用处理 —— 那就变成在工作线程里写控件了。
    """

    page = QtCore.pyqtSignal(object)

    def __init__(self, handler=None, parent=None):
        super().__init__(parent)
        self._handler = handler
        self.page.connect(self._deliver)

    @QtCore.pyqtSlot(object)
    def _deliver(self, item: Any) -> None:
        if self._handler is not None:
            self._handler(item)


class SubmitPanel(QtWidgets.QWidget):
    """提交页（主窗口里的一页）。流程走到最后一步时点亮，不需要再交回结果。"""

    # 要给用户看的通知（文本, 类型 ok/err/warn/info）—— 主窗口收到就在右下角弹一条
    # （用户 2026-09-29：「提交页的通知也应该一条一条的在左下角通过弹窗的形式弹出来」，
    #   当天又把位置改成了右下角）
    notified = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.api = None
        self._busy = False
        self._finished = False
        self._result_url = ""                  # 提交成功后拿到的条目地址（给「打开条目」按钮）
        self._workers: List[FunctionWorker] = []
        self._preview_result: Dict[str, Any] = {}
        # 状态行的「世代」：提交 / 保存这些用户主动动作会把世代 +1。
        # 预览是早晚都会回来的后台请求，回来时如果世代变了（期间已经提交过）
        # 就不要再把状态行改写成「预览已更新」——那会把真正的结果盖掉。
        self._status_token = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(700)
        self._timer.timeout.connect(lambda: self._request_preview(silent=False))
        self._build_ui()

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("条目：—", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1)
        self.family_check = QtWidgets.QCheckBox("同步大家族模板", self)
        self.family_check.setChecked(True)
        self.family_check.setToolTip("提交条目后把这首歌写进「== 注释 ==」的模板："
                                     "歌手模板按荣誉或「部分非殿堂曲」；P主模板写进投稿年份那一格")
        self.family_check.toggled.connect(lambda _checked: self._describe_family())
        top.addWidget(self.family_check)
        self.auto_preview_check = QtWidgets.QCheckBox("实时预览", self)
        self.auto_preview_check.setChecked(True)
        top.addWidget(self.auto_preview_check)
        self.preview_button = QtWidgets.QPushButton("刷新预览", self)
        self.preview_button.clicked.connect(lambda: self._request_preview(silent=False))
        top.addWidget(self.preview_button)
        self.save_button = QtWidgets.QPushButton("保存到本地", self)
        self.save_button.clicked.connect(self._save_local)
        top.addWidget(self.save_button)
        self.submit_button = QtWidgets.QPushButton("提交到 Vocawiki", self)
        self.submit_button.setDefault(True)
        theme.mark_accent(self.submit_button)      # 默认按钮 = 主按钮（QSS 会加粗，字体也得跟着粗）
        self.submit_button.clicked.connect(self._submit)
        top.addWidget(self.submit_button)
        root.addLayout(top)

        summary_row = QtWidgets.QHBoxLayout()
        summary_row.addWidget(QtWidgets.QLabel("提交摘要", self))
        self.summary_edit = QtWidgets.QLineEdit(self)
        summary_row.addWidget(self.summary_edit, 1)
        self.redirect_label = QtWidgets.QLabel("", self)
        self.cover_label = QtWidgets.QLabel("", self)
        summary_row.addWidget(self.redirect_label)
        summary_row.addWidget(self.cover_label)
        root.addLayout(summary_row)

        self.family_label = QtWidgets.QLabel("", self)
        self.family_label.setWordWrap(True)
        self.family_label.setStyleSheet("QLabel { color: #54595d; }")
        root.addWidget(self.family_label)
        self.disambig_label = QtWidgets.QLabel("", self)
        self.disambig_label.setWordWrap(True)
        self.disambig_label.setStyleSheet("QLabel { color: #54595d; }")
        root.addWidget(self.disambig_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        editor_holder = QtWidgets.QWidget(splitter)
        editor_layout = QtWidgets.QVBoxLayout(editor_holder)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_head = QtWidgets.QLabel(
            "Wikitext（可直接编辑 · Ctrl+S 保存到本地 · Ctrl+Enter 提交）", editor_holder)
        editor_layout.addWidget(editor_head)
        self.editor = QtWidgets.QPlainTextEdit(editor_holder)
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.editor.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.editor, theme.MONO_SIZE_PX, mono=True)
        self.editor.textChanged.connect(self._schedule_preview)
        editor_layout.addWidget(self.editor, 1)
        splitter.addWidget(editor_holder)

        preview_holder = QtWidgets.QWidget(splitter)
        preview_layout = QtWidgets.QVBoxLayout(preview_holder)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        head_row = QtWidgets.QHBoxLayout()
        head_row.addWidget(QtWidgets.QLabel("预览", preview_holder))
        self.preview_hint = QtWidgets.QLabel("尚未预览", preview_holder)
        self.preview_hint.setStyleSheet("QLabel { color: #72777d; }")
        head_row.addStretch(1)
        head_row.addWidget(self.preview_hint)
        preview_layout.addLayout(head_row)
        self.preview_view = _create_preview_view(preview_holder)
        if self.preview_view is not None:
            preview_layout.addWidget(self.preview_view, 1)
        else:
            # 没有 QtWebEngine 时给个占位，免得这里空一块（下面的按钮直接在浏览器里打开）
            self.preview_placeholder = QtWidgets.QLabel(
                "当前环境没有 QtWebEngine，预览改在系统浏览器里打开。", preview_holder)
            self.preview_placeholder.setAlignment(QtCore.Qt.AlignCenter)
            self.preview_placeholder.setStyleSheet("QLabel { color: #72777d; }")
            preview_layout.addWidget(self.preview_placeholder, 1)
        self.browser_button = QtWidgets.QPushButton("在浏览器里打开预览", preview_holder)
        self.browser_button.clicked.connect(self._open_preview_in_browser)
        self.browser_button.setVisible(self.preview_view is None)
        preview_layout.addWidget(self.browser_button)
        splitter.addWidget(preview_holder)
        splitter.setSizes([640, 640])
        root.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("就绪", self)
        footer.addWidget(self.status_label)
        self.login_label = QtWidgets.QLabel("", self)
        self.login_label.setStyleSheet("QLabel { color: #ac6600; font-weight: 600; }")
        footer.addWidget(self.login_label)
        footer.addStretch(1)
        # 提交成功后用它打开条目（以前是那个居中弹窗里的按钮，用户 2026-09-29 要求
        # 不要再弹居中窗口，所以把「打开条目」挪到底部这一行）
        self.open_button = QtWidgets.QPushButton("在浏览器中打开条目", self)
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(self._open_entry_in_browser)
        footer.addWidget(self.open_button)
        root.addLayout(footer)

        shortcut_save = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+S"), self)
        shortcut_save.activated.connect(self._save_local)
        shortcut_submit = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self)
        shortcut_submit.activated.connect(self._submit)

    # ------------------------------------------------------------ 启动

    def start(self, payload: Dict[str, Any]) -> None:
        api = (payload or {}).get("api")
        self.api = api
        self._finished = False
        self._result_url = ""
        self.open_button.setVisible(False)
        if api is None:
            return
        context = api.get_context()
        self._context = context
        self.title_label.setText(f"条目：{context.get('page') or '—'} · 文件：{context.get('file') or '—'}")
        self.summary_edit.setText(context.get("summary") or "")
        self.editor.setPlainText(getattr(api, "_wikitext", "") or "")
        can_submit = bool(context.get("canSubmit"))
        self.submit_button.setEnabled(can_submit)
        self.login_label.setText("" if can_submit else "未登录 Vocawiki，只能预览与编辑，无法提交")
        self._describe_redirect(context)
        self._describe_cover(context)
        self._describe_disambig(context)
        self._describe_family()
        self._apply_kind(context)
        self._request_preview(silent=False)
        self.editor.setFocus()

    def _apply_kind(self, context: Dict[str, Any]) -> None:
        """按提交的东西调界面：模板页（P主模板）不需要条目那一套信息。

        重定向 / 封面 / 同名条目 / 大家族模板都是**条目**才有的东西，
        模板页上留着那四行只是干扰（`ProducerTemplateApi.get_context()` 会带 `kind`）。
        """
        is_template = context.get("kind") == "template"
        for label in (self.redirect_label, self.cover_label, self.disambig_label,
                      self.family_label):
            label.setVisible(not is_template)
        if is_template:
            self.family_check.setVisible(False)
            self.title_label.setText(f"模板页：{context.get('page') or '—'}"
                                     f" · 文件：{context.get('file') or '—'}")

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录 → 重新开始」时由主窗口调）。

        新一轮还没跑到这一步，页面上不该再摆着上一首歌的条目 / 预览（用户 2026-09 反馈）。
        """
        self.api = None
        self._context = {}
        self._finished = True                 # 旧页面上的按钮不该还能提交
        self._status_token += 1               # 让迟到的预览结果失效，别把状态行改回去
        self._timer.stop()
        self.title_label.setText("条目：—")
        self.summary_edit.clear()
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)
        for label in (self.redirect_label, self.cover_label, self.disambig_label,
                      self.family_label, self.login_label):
            label.clear()
        self.submit_button.setEnabled(False)
        self.preview_hint.setText("尚未预览")
        self._preview_result = {}
        self._preview_cover = None
        self._result_url = ""
        if hasattr(self, "open_button"):
            self.open_button.setVisible(False)
        self._show_preview_html("")
        self.set_status("等新一轮生成…")

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        # 长报错（提交失败：接口返回 500 …）只显示前半句，全文挂 tooltip
        widgets.set_status_text(self.status_label, text, compact=(kind == "err"))
        self.notify(text, kind)

    def notify(self, text: str, kind: str = "") -> None:
        """把要提醒用户的事发出去（左下角的通知卡片用）。

        只有「有结果」的消息（ok / err / warn）才弹：进度提示（「提交中…」「预览已更新」…）
        不弹，不然一直在左下角闪。多步消息（提交成功那一长串，步骤间用「；」接）拆成一条一条。
        """
        if kind not in ("ok", "err", "warn"):
            return
        for part in str(text or "").split("；"):
            part = part.strip()
            if part:
                self.notified.emit(part, kind)

    def _describe_redirect(self, context: Dict[str, Any]) -> None:
        if context.get("createRedirect") and context.get("redirect"):
            target = context.get("redirectTarget") or context.get("page")
            self.redirect_label.setText(
                f"将创建重定向：{context.get('redirect')} → {target}")
        elif context.get("createRedirect"):
            self.redirect_label.setText("已开启重定向，但日文原名与条目名相同，将跳过")
        else:
            self.redirect_label.setText("未开启日文原名重定向")

    def _describe_cover(self, context: Dict[str, Any]) -> None:
        cover = context.get("cover")
        if cover and cover.get("exists"):
            characters = cover.get("characters") or []
            suffix = f"（歌姬分类：{'、'.join(characters)}）" if characters else ""
            self.cover_label.setText(f"提交时一并上传封面：{cover.get('wikiName')}{suffix}")
        elif cover:
            self.cover_label.setText("未找到本地封面文件，将跳过上传")
        else:
            self.cover_label.setText("未下载封面，将跳过上传")

    def _describe_disambig(self, context: Dict[str, Any]) -> None:
        info = context.get("disambig") or {}
        if not info.get("needed"):
            self.disambig_label.setText(
                "同名条目：" + (str(info.get("note")) if info.get("note") else "没有同名条目，按普通条目上传"))
            return
        self.disambig_label.setText(
            f"同名条目：本条目将上传到「{info.get('title')}」（共 {info.get('total') or 1} 个同名条目），"
            "正在检查处理方式…")
        self._run_background(self.api.disambig_plan, self._on_disambig_plan)

    def _on_disambig_plan(self, result: Dict[str, Any]) -> None:
        lines = (result or {}).get("lines") or []
        self.disambig_label.setText("同名条目：" + ("；".join(lines) if lines else "无需处理"))

    def _describe_family(self) -> None:
        family = (self._context or {}).get("family") or {}
        if not family.get("available"):
            self.family_check.setVisible(False)
            self.family_label.setText("大家族模板：注释区没有可同步的模板")
            return
        self.family_check.setVisible(True)
        if not self.family_check.isChecked():
            self.family_label.setText("大家族模板：已关闭同步")
            return
        self.family_label.setText("大家族模板：正在检查…")
        self._run_background(self.api.family_plan, lambda result: self.family_label.setText(
            "大家族模板：" + ("；".join((result or {}).get("lines") or []) or
                          str((result or {}).get("error") or "没有需要改动的地方"))))

    # ------------------------------------------------------------ 预览

    def _schedule_preview(self) -> None:
        if self.auto_preview_check.isChecked() and not self._busy:
            self._timer.start()

    def _request_preview(self, silent: bool = False) -> None:
        if self.api is None:
            return
        text = self.editor.toPlainText()
        token = self._status_token
        self.preview_hint.setText("预览中…")
        # silent 由 _run_background 按位置传进来，多出来的位置参数由 *extra 吃掉
        self._run_background(lambda: self.api.preview(text),
                             lambda result, *extra: self._on_preview(result, silent, token),
                             silent=False)

    def _on_preview(self, result: Dict[str, Any], silent: bool = False,
                    token: Optional[int] = None) -> None:
        fresh = token is None or token == self._status_token
        if not result or result.get("error"):
            message = (result or {}).get("error") or "未知错误"
            self.preview_hint.setText("预览失败")
            self._show_preview_html(error_doc(str(message)))
            if not silent and fresh:
                self.set_status(f"预览失败：{message}", "err")
            return
        origin = (self._context or {}).get("origin") or ""
        document = build_preview_html(result, origin)
        self._show_preview_html(document)
        cover = result.get("cover")
        self._preview_cover = cover
        self.preview_hint.setText("已更新 " + QtCore.QTime.currentTime().toString("HH:mm:ss")
                                  + ("（含站点CSS）" if result.get("css") else ""))
        if not silent and fresh:
            self.set_status("预览已更新")

    def _show_preview_html(self, document: str) -> None:
        self._preview_document = document
        view = self.preview_view
        if view is None:
            return
        view.setHtml(document)
        cover = getattr(self, "_preview_cover", None)
        if cover:
            # 等页面渲染完再把「尚未上传」的封面换成本地图片
            QtCore.QTimer.singleShot(
                400, lambda: view.page().runJavaScript(
                    PREVIEW_JS % json.dumps(cover, ensure_ascii=False)))

    def _open_preview_in_browser(self) -> None:
        document = getattr(self, "_preview_document", "")
        if not document:
            self._request_preview(silent=False)
            return
        path = Path(tempfile.gettempdir()) / "vocawiki-preview.html"
        try:
            path.write_text(document, encoding="utf-8")
        except OSError as e:
            self.set_status(f"写入预览文件失败：{e}", "err")
            return
        webbrowser.open(path.as_uri())

    # ------------------------------------------------------------ 保存 / 提交

    def _save_local(self) -> None:
        if self.api is None:
            return
        self._status_token += 1               # 以后回来的预览结果别再改状态行
        result = self.api.save(self.editor.toPlainText())
        if result.get("ok"):
            self.set_status("✓ " + str(result.get("message") or "已保存到本地文件"), "ok")
        else:
            self.set_status("保存失败：" + str(result.get("error") or "未知错误"), "err")

    def _submit(self) -> None:
        if self.api is None or self._busy or self._finished:
            return
        self._status_token += 1               # 以后回来的预览结果别再改状态行
        self._busy = True
        self.submit_button.setEnabled(False)
        self.set_status("提交中…")
        text = self.editor.toPlainText()
        summary = self.summary_edit.text()
        family = bool((self._context or {}).get("family", {}).get("available")
                      and self.family_check.isChecked())
        self._run_background(lambda: self.api.submit(text, summary, family), self._on_submitted)

    def _on_submitted(self, result: Dict[str, Any]) -> None:
        self._busy = False
        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "未知错误"
            self.set_status(f"提交失败：{error}", "err")
            self.submit_button.setEnabled(True)
            self.login_label.setText("窗口保持打开，可修改后按 Ctrl+Enter 重试提交。")
            self.login_label.setStyleSheet("QLabel { color: #ac6600; font-weight: 600; }")
            return
        self._finished = True
        message = str(result.get("message") or "已完成")
        self._result_url = str(result.get("url") or "")
        self.open_button.setVisible(bool(self._result_url))
        self._finish_status(message)
        backlinks = result.get("backlinks") or []
        if backlinks:
            self.submit_button.setEnabled(True)
            self._show_backlink_dialog(result)

    def _open_entry_in_browser(self) -> None:
        if self._result_url:
            webbrowser.open(self._result_url)

    def _finish_status(self, message: str) -> None:
        """收尾：右下角一条一条弹结果，底部状态栏只写「已完成所有操作」。

        用户 2026-09-29 要求：提交完**不要**再弹居中对话框，底部那一行也别写一长串 ——
        一句「已完成所有操作」就行，详细步骤放 tooltip 里（右下角的卡片已经一条一条弹了）。
        """
        self.status_label.setStyleSheet("QLabel { color: #14866d; }")
        widgets.set_status_text(self.status_label, "✓ 已完成所有操作")
        self.status_label.setToolTip(message)
        self.notify(message, "ok")

    # ------------------------------------------------------------ 链入页面修正

    def _show_backlink_dialog(self, result: Dict[str, Any]) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(str(result.get("backlinkTitle") or "修正链入页面"))
        layout = QtWidgets.QVBoxLayout(dialog)
        header = QtWidgets.QLabel(
            str(result.get("backlinkHeader")
                or f"把「{result.get('backlinkOld')}」的链入改到「{result.get('backlinkNew')}」"),
            dialog)
        layout.addWidget(header)
        # 文案可由后端指定（P主模板那条路是「把模板加进条目」而不是「替换链接」）
        action_text = str(result.get("backlinkAction") or "替换选中页面的链接")
        skip_note = str(result.get("backlinkSkipNote") or " —— 无法自动替换")
        labels = {"done": str(result.get("backlinkDone") or "替换完成"),
                  "failed": str(result.get("backlinkFail") or "替换失败")}
        listing = QtWidgets.QListWidget(dialog)
        for item in result.get("backlinks") or []:
            title = str(item.get("title"))
            count = int(item.get("count") or 0)
            can_fix = count > 0
            detail = str(item.get("note") or f"{count} 处{item.get('kind') or ''}")
            entry = QtWidgets.QListWidgetItem(
                f"{title}（{detail}）" + ("" if can_fix else skip_note))
            entry.setData(QtCore.Qt.UserRole, title)
            entry.setFlags(entry.flags() | QtCore.Qt.ItemIsUserCheckable)
            entry.setCheckState(QtCore.Qt.Checked if can_fix else QtCore.Qt.Unchecked)
            if not can_fix:
                entry.setFlags(entry.flags() & ~QtCore.Qt.ItemIsEnabled)
            listing.addItem(entry)
        layout.addWidget(listing)
        # 逐页结果：一个条目一条，改完一条写一条（用户 2026-09-29 要求「一个条目一个条目的冒」）
        log = QtWidgets.QPlainTextEdit(dialog)
        log.setReadOnly(True)
        log.setMaximumHeight(96)
        log.setPlaceholderText("结果会一个条目一条写在这里（改完一条出现一条）")
        layout.addWidget(log)
        status = QtWidgets.QLabel(f"共 {listing.count()} 个链入页面，勾选后点「{action_text}」。",
                                  dialog)
        layout.addWidget(status)
        buttons = QtWidgets.QHBoxLayout()
        select_all = QtWidgets.QPushButton("全选", dialog)
        select_all.clicked.connect(lambda: _set_all_checks(listing, True))
        buttons.addWidget(select_all)
        select_none = QtWidgets.QPushButton("全不选", dialog)
        select_none.clicked.connect(lambda: _set_all_checks(listing, False))
        buttons.addWidget(select_none)
        buttons.addStretch(1)
        apply_button = QtWidgets.QPushButton(action_text, dialog)
        buttons.addWidget(apply_button)
        close_button = QtWidgets.QPushButton("关闭", dialog)
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def apply_fix() -> None:
            titles = [listing.item(row).data(QtCore.Qt.UserRole)
                      for row in range(listing.count())
                      if listing.item(row).checkState() == QtCore.Qt.Checked]
            if not titles:
                status.setText("没有选中任何页面。")
                return
            apply_button.setEnabled(False)
            log.clear()
            counts = {"ok": 0, "failed": 0}
            progress = _PageProgress(_on_page_done)   # 工作线程 emit → 主线程写日志
            status.setText(f"正在处理 {len(titles)} 个页面…")
            self._run_background(
                lambda: self.api.fix_backlinks(json.dumps(titles, ensure_ascii=False),
                                               progress.page.emit),
                lambda res: _on_fixed(res, status, apply_button, log, counts, progress))

        def _on_page_done(item: Dict[str, Any]) -> None:
            """每处理完一页就写一条（成功打勾、失败打叉，都写清楚是哪个条目）。"""
            line = backlink_page_text(item)
            log.appendPlainText(line)
            self.notify(line, "ok" if item.get("ok") else "err")   # 右下角也弹一条
            if item.get("ok"):
                counts["ok"] += 1
            else:
                counts["failed"] += 1
            status.setText(f"已处理 {counts['ok'] + counts['failed']} 个"
                           f"（成功 {counts['ok']}，未改动 {counts['failed']}）…")

        def _on_fixed(res: Dict[str, Any], status_label: QtWidgets.QLabel,
                      button: QtWidgets.QPushButton, log_box: QtWidgets.QPlainTextEdit,
                      counter: Dict[str, int], watcher: "_PageProgress") -> None:
            button.setEnabled(True)
            try:
                watcher.page.disconnect()
            except TypeError:                        # 已经断开过（信号上没有连接）
                pass
            if not res or not res.get("ok"):
                status_label.setText(labels["failed"] + "："
                                     + str((res or {}).get("error") or "未知错误"))
                return
            summary = f"{labels['done']}：成功 {counter['ok']} 个"
            if counter["failed"]:
                summary += f"，{counter['failed']} 个未改动"
            log_box.appendPlainText(summary)
            status_label.setText(summary)
            self._request_preview(silent=True)

        apply_button.clicked.connect(apply_fix)
        dialog.exec_()

    # ------------------------------------------------------------ 后台调用

    def _run_background(self, func, callback, silent: bool = False) -> None:
        """把慢调用丢到线程里；回调在主线程执行。"""
        worker = FunctionWorker(func, parent=self)
        self._workers.append(worker)

        def handle(result: Any) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            try:
                if silent:
                    callback(result, True)
                else:
                    callback(result)
            except TypeError:
                callback(result)

        worker.done.connect(handle)
        worker.finished.connect(worker.deleteLater)
        worker.start()


def _set_all_checks(listing: QtWidgets.QListWidget, checked: bool) -> None:
    for row in range(listing.count()):
        item = listing.item(row)
        if item.flags() & QtCore.Qt.ItemIsEnabled:
            item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)


# 预览右键菜单：WebEngine 自带的菜单由 Chromium 给出、文案是英文（PyQt5 没带
# `qtwebengine_zh_CN.qm`，只有 en / de / ru…），所以拿 Qt 拼好的菜单再把文案换成中文。
# 与 `theme.QT_EDIT_MENU_ZH` / `theme.QT_STANDARD_BUTTON_ZH` 同属一套「Qt 原文 → 中文」表
# （纪律写在 `theme.py` 那段注释里）：**键 = Qt 源码里的原文**，左边就是
# `QWebEnginePage.WebAction` 的 `action.text()` 原文（实测 Qt 5.15 / PyQt5 5.15）。
# 这里认不出的原文**原样返回**（菜单总得显示点什么），不像翻译器那样返回 `None`。
QT_PREVIEW_MENU_ZH = {
    "Back": "返回",
    "Forward": "前进",
    "Stop": "停止",
    "Reload": "重新载入",
    "Reload and Bypass Cache": "强制重新载入",
    "Cut": "剪切",
    "Copy": "复制",
    "Paste": "粘贴",
    "Paste and match style": "粘贴为纯文本",
    "Undo": "撤消",
    "Redo": "恢复",
    "Select all": "全选",
    "Unselect": "取消选择",
    "Open link in this window": "在本窗口打开链接",
    "Open link in new window": "在新窗口打开链接",
    "Open link in new tab": "在新标签页打开链接",
    "Open link in new background tab": "在后台标签页打开链接",
    "Copy link address": "复制链接地址",
    "Save link": "保存链接",
    "Copy image": "复制图片",
    "Copy image address": "复制图片地址",
    "Save image": "保存图片",
    "Copy media address": "复制媒体地址",
    "Save media": "保存媒体",
    "Show controls": "显示控件",
    "Loop": "循环播放",
    "Toggle Play/Pause": "播放 / 暂停",
    "Toggle Mute": "静音 / 取消静音",
    "Inspect": "检查元素",
    "Exit full screen": "退出全屏",
    "Close Page": "关闭页面",
    "Save page": "保存网页",
    "View page source": "查看网页源代码",
    "&Bold": "加粗",
    "&Italic": "斜体",
    "&Underline": "下划线",
}


def preview_menu_text(text: str) -> str:
    """右键菜单条目文案 → 中文（认不出的原样保留，`\\t快捷键` 那一截不动）。"""
    label, tab, shortcut = str(text or "").partition("\t")
    return QT_PREVIEW_MENU_ZH.get(label.strip(), label) + (tab + shortcut if tab else "")


def _preview_menu(page) -> Optional[QtWidgets.QMenu]:
    """让 Qt 按当前上下文拼好右键菜单（`createStandardContextMenu`），再把文案换成中文。

    用它比自己拼一份强：可编辑 / 链接 / 图片 / 视频各种上下文该出哪几条由 Qt 决定，
    启用状态也是对的；没有上下文数据时（还没右键过）它返回 None。
    """
    if page is None or not hasattr(page, "createStandardContextMenu"):
        return None
    try:
        menu = page.createStandardContextMenu()
    except Exception as error:                       # noqa: BLE001 - 包装层的怪问题别崩界面
        logging.warning("取网页右键菜单失败：%s", error)
        return None
    if menu is None:
        return None
    for action in menu.actions():
        action.setText(preview_menu_text(action.text()))
    if not menu.actions():                           # 空菜单（认不出上下文）就别弹了
        menu.deleteLater()
        return None
    return menu


def _install_preview_menu(view) -> None:
    """把预览的右键菜单换成中文那份（WebEngine 默认菜单是英文）。

    用 `Qt.CustomContextMenu` 截下右键事件 —— 设了它之后 `QWebEngineView` 自己那套
    英文菜单就不会弹出（见 `QWidget::event` 对 ContextMenu 的处理），由我们负责弹。
    界面语言是英文（`lang: en`）时什么都不做：直接用内核自带的英文菜单，与 Qt 翻译一条路子。
    任何异常都不往外抛：最坏就是右键没反应，用户还能用「在浏览器里打开预览」。
    """
    language = theme.ui_language()
    if language and not language.startswith("zh"):
        return

    def show(pos) -> None:
        try:
            menu = _preview_menu(view.page())
            if menu is None:
                return
            menu.exec_(view.mapToGlobal(pos))
            menu.deleteLater()                       # createStandardContextMenu 给的菜单归我们删
        except RuntimeError:
            pass                                     # 菜单已经随关闭被 Qt 删掉了
        except Exception as error:                   # noqa: BLE001 - 槽里不能往外抛
            logging.warning("弹出预览右键菜单失败：%s", error)

    view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    view.customContextMenuRequested.connect(show)


def _create_preview_view(parent: QtWidgets.QWidget):
    """有 QtWebEngine 就用它渲染预览，没有就返回 None（界面给「在浏览器里打开预览」）。

    VOCAWIKI_NO_WEBENGINE=1 时也返回 None：无头环境（单测 / CI）里别去起浏览器内核。
    """
    if os.environ.get("VOCAWIKI_NO_WEBENGINE"):
        return None
    try:
        from PyQt5 import QtWebEngineWidgets
    except ImportError:
        logging.warning("未安装 PyQtWebEngine，提交预览改用系统浏览器打开。")
        return None
    view = QtWebEngineWidgets.QWebEngineView(parent)
    _install_preview_menu(view)
    return view
