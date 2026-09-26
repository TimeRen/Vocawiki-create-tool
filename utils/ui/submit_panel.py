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


class SubmitPanel(QtWidgets.QWidget):
    """提交页（主窗口里的一页）。流程走到最后一步时点亮，不需要再交回结果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.api = None
        self._busy = False
        self._finished = False
        self._workers: List[FunctionWorker] = []
        self._preview_result: Dict[str, Any] = {}
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(700)
        self._timer.timeout.connect(lambda: self._request_preview(silent=False))
        self._build_ui()

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
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
        self.editor.setStyleSheet("QPlainTextEdit { font-family: Consolas, 'Cascadia Mono', "
                                  "monospace; font-size: 12px; background: #ffffff; }")
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
        self._request_preview(silent=False)
        self.editor.setFocus()

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self.status_label.setText(text)

    def _describe_redirect(self, context: Dict[str, Any]) -> None:
        if context.get("createRedirect") and context.get("redirect"):
            self.redirect_label.setText(
                f"将创建重定向：{context.get('redirect')} → {context.get('page')}")
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
        self.preview_hint.setText("预览中…")
        self._run_background(lambda: self.api.preview(text), self._on_preview,
                             silent=silent)

    def _on_preview(self, result: Dict[str, Any], silent: bool = False) -> None:
        if not result or result.get("error"):
            message = (result or {}).get("error") or "未知错误"
            self.preview_hint.setText("预览失败")
            self._show_preview_html(error_doc(str(message)))
            if not silent:
                self.set_status(f"预览失败：{message}", "err")
            return
        origin = (self._context or {}).get("origin") or ""
        document = build_preview_html(result, origin)
        self._show_preview_html(document)
        cover = result.get("cover")
        self._preview_cover = cover
        self.preview_hint.setText("已更新 " + QtCore.QTime.currentTime().toString("HH:mm:ss")
                                  + ("（含站点CSS）" if result.get("css") else ""))
        if not silent:
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
        result = self.api.save(self.editor.toPlainText())
        if result.get("ok"):
            self.set_status("✓ " + str(result.get("message") or "已保存到本地文件"), "ok")
        else:
            self.set_status("保存失败：" + str(result.get("error") or "未知错误"), "err")

    def _submit(self) -> None:
        if self.api is None or self._busy or self._finished:
            return
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
        self.set_status("✓ " + message, "ok")
        backlinks = result.get("backlinks") or []
        if backlinks:
            self.submit_button.setEnabled(True)
            self._show_backlink_dialog(result)
            return
        self._show_done_dialog(message, result.get("url"))

    def _show_done_dialog(self, message: str, url: Optional[str]) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("已完成 Vocawiki 编辑")
        box.setText(message)
        open_button = box.addButton("在浏览器中打开条目", QtWidgets.QMessageBox.ActionRole)
        box.addButton("关闭", QtWidgets.QMessageBox.AcceptRole)
        box.exec_()
        if box.clickedButton() is open_button and url:
            webbrowser.open(url)

    # ------------------------------------------------------------ 链入页面修正

    def _show_backlink_dialog(self, result: Dict[str, Any]) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("修正链入页面")
        layout = QtWidgets.QVBoxLayout(dialog)
        header = QtWidgets.QLabel(
            f"把「{result.get('backlinkOld')}」的链入改到「{result.get('backlinkNew')}」", dialog)
        layout.addWidget(header)
        listing = QtWidgets.QListWidget(dialog)
        for item in result.get("backlinks") or []:
            title = str(item.get("title"))
            can_fix = int(item.get("count") or 0) > 0
            entry = QtWidgets.QListWidgetItem(
                f"{title}（{item.get('count')} 处{item.get('kind') or ''}）"
                + ("" if can_fix else " —— 无法自动替换"))
            entry.setData(QtCore.Qt.UserRole, title)
            entry.setFlags(entry.flags() | QtCore.Qt.ItemIsUserCheckable)
            entry.setCheckState(QtCore.Qt.Checked if can_fix else QtCore.Qt.Unchecked)
            if not can_fix:
                entry.setFlags(entry.flags() & ~QtCore.Qt.ItemIsEnabled)
            listing.addItem(entry)
        layout.addWidget(listing)
        status = QtWidgets.QLabel(f"共 {listing.count()} 个链入页面，勾选后点「替换选中页面的链接」。",
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
        apply_button = QtWidgets.QPushButton("替换选中页面的链接", dialog)
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
            status.setText(f"正在替换 {len(titles)} 个页面…")
            self._run_background(
                lambda: self.api.fix_backlinks(json.dumps(titles, ensure_ascii=False)),
                lambda res: _on_fixed(res, status, apply_button))

        def _on_fixed(res: Dict[str, Any], status_label: QtWidgets.QLabel,
                      button: QtWidgets.QPushButton) -> None:
            button.setEnabled(True)
            if not res or not res.get("ok"):
                status_label.setText("替换失败：" + str((res or {}).get("error") or "未知错误"))
                return
            status_label.setText(str(res.get("message") or "已完成"))
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
    return view
