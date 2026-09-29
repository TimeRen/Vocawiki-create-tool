"""「曲目」页：P主模板的曲目 / 专辑清单（可增删改、可去维基补中文条目名）。

这是侧栏第二个功能「生成P主模板」的第一页：

    ┌ P主：雄之助（VocaDB 23981）   P主条目 [雄之助]   模板名 [雄之助] ┐
    ├ [从维基补全条目名] [添加曲目] [删除选中] [刷新条目状态]        ┤
    ├ 年份 │ 中文条目 │ 日文原名 │ 投稿日期 │ 状态   （可直接改格子） ┤
    ├ 专辑（一行一个，模板里链到 P主条目的小节）                      ┤
    ├ 模板 wikitext 预览（跟着上面的改动实时变，只读）                ┤
    └ 状态行 ………………………………… [取消] [保存并继续]                        ┘

曲目怎么排：模板按**投稿年份**分格（`|group1 = 2025年`），格子内按日期排，
所以这里不提供手工拖排序 —— 改日期就能改位置。年份格子由日期算出来，只读。
"""
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import producer_template as pt
from utils import wiki_api
from utils.ui import theme, widgets
from utils.ui.workers import FunctionWorker

COLUMNS = ("年份", "中文条目", "日文原名", "投稿日期", "状态")
COLUMN_WIDTHS = (74, 240, 240, 130, 90)
COLUMN_TIPS = (
    "由「投稿日期」算出来，模板里就是这个年份格子",
    "维基上的条目名；留空就用日文原名当链接目标（一般是红链，等人来建）",
    "日文原名（VocaDB 的 defaultName）",
    "日期：2024-08-28 / 2024年8月28日 / 2024 都认，排序与分格都看它",
    "已建 = 维基上已经有这一页；待建 = 还是红链",
)


class _Progress(QtCore.QObject):
    """工作线程 → 主线程的一行进度（搜索维基时用）。

    必须连到 QObject 的方法上，PyQt 才会按线程关系走队列连接；连普通函数会变成
    在工作线程里直接写控件（见 `submit_panel._PageProgress` 的同一段注释）。
    """

    message = QtCore.pyqtSignal(str)

    def __init__(self, handler, parent=None):
        super().__init__(parent)
        self.message.connect(handler)


class ProducerPanel(QtWidgets.QWidget):
    """曲目页。保存发 `saved(work)`，取消发 `cancelled`。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work: Optional[pt.ProducerWork] = None
        self._loading = False
        self._rows: List[pt.ProducerSong] = []      # 表格第 row 行对应哪一首曲目
        self._workers: List[FunctionWorker] = []
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.artist_label = QtWidgets.QLabel("P主：—", self)
        font = self.artist_label.font()
        font.setBold(True)
        self.artist_label.setFont(font)
        top.addWidget(self.artist_label, 1)
        for label, attr, tip in (("P主条目", "page_edit",
                                  "模板标题与专辑小节都链到这一页（`[[雄之助]]`、"
                                  "`{{linksplit|prefix=雄之助}}`）"),
                                 ("模板名", "name_edit",
                                  "写进 `|name=` 与 `{{PAGENAME}}`，也是提交时的页面名"
                                  "（Template:<模板名>）")):
            top.addWidget(QtWidgets.QLabel(label, self))
            edit = QtWidgets.QLineEdit(self)
            edit.setMaximumWidth(200)
            edit.setToolTip(tip)
            edit.textChanged.connect(self._on_meta_changed)
            setattr(self, attr, edit)
            top.addWidget(edit)
        root.addLayout(top)

        tools = QtWidgets.QHBoxLayout()
        self.search_button = QtWidgets.QPushButton("从维基补全条目名", self)
        self.search_button.setToolTip(
            "对还没有中文条目名的曲目，逐首按日文原名搜索维基（generator=search），\n"
            "命中就填进「中文条目」。曲子多时要等一会儿，过程中状态行会显示进度。")
        self.search_button.clicked.connect(self._search_names)
        tools.addWidget(self.search_button)
        self.add_button = QtWidgets.QPushButton("添加曲目", self)
        self.add_button.setToolTip("手动补一首 VocaDB 上没有的曲子（比如刚投稿的）")
        self.add_button.clicked.connect(self._add_song)
        tools.addWidget(self.add_button)
        self.remove_button = QtWidgets.QPushButton("删除选中", self)
        self.remove_button.clicked.connect(self._remove_songs)
        tools.addWidget(self.remove_button)
        self.refresh_button = QtWidgets.QPushButton("刷新条目状态", self)
        self.refresh_button.setToolTip("重新批量查一遍「已建 / 待建」")
        self.refresh_button.clicked.connect(self._refresh_status)
        tools.addWidget(self.refresh_button)
        tools.addStretch(1)
        root.addLayout(tools)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for index, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(index, width)
            self.table.horizontalHeaderItem(index).setToolTip(COLUMN_TIPS[index])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(240)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 3)

        album_head = QtWidgets.QHBoxLayout()
        album_head.addWidget(QtWidgets.QLabel("专辑（一行一个，模板里用 linksplit 链到 P主条目的同名小节）",
                                              self))
        album_head.addStretch(1)
        self.album_add_button = QtWidgets.QPushButton("添加专辑", self)
        self.album_add_button.clicked.connect(self._add_album)
        album_head.addWidget(self.album_add_button)
        self.album_remove_button = QtWidgets.QPushButton("删除专辑", self)
        self.album_remove_button.clicked.connect(self._remove_album)
        album_head.addWidget(self.album_remove_button)
        root.addLayout(album_head)

        self.album_list = QtWidgets.QListWidget(self)
        self.album_list.setMaximumHeight(110)
        self.album_list.setToolTip("双击改名字")
        self.album_list.itemChanged.connect(self._on_album_changed)
        root.addWidget(self.album_list)

        preview_head = QtWidgets.QHBoxLayout()
        preview_head.addWidget(QtWidgets.QLabel("模板 wikitext（跟着上面的改动实时变，只读）", self))
        self.count_label = QtWidgets.QLabel("", self)
        self.count_label.setStyleSheet(theme.quiet_label_style())
        preview_head.addStretch(1)
        preview_head.addWidget(self.count_label)
        root.addLayout(preview_head)

        self.preview = QtWidgets.QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(170)
        self.preview.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.preview, theme.MONO_SIZE_PX, mono=True)
        root.addWidget(self.preview, 2)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("就绪", self)
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        self.cancel_button.clicked.connect(self.cancelled.emit)
        footer.addWidget(self.cancel_button)
        self.save_button = QtWidgets.QPushButton("保存并继续", self)
        self.save_button.setDefault(True)
        theme.mark_accent(self.save_button)
        self.save_button.clicked.connect(self._on_save)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

    # ------------------------------------------------------------ 开 / 关
    def start(self, payload: Dict[str, Any]) -> None:
        """打开这一页：`payload["work"]` 是 `ProducerWork`。"""
        work = (payload or {}).get("work")
        self.work = work
        self._loading = True
        try:
            self.artist_label.setText(
                f"P主：{work.artist.name}（VocaDB {work.artist.id}）" if work else "P主：—")
            self.page_edit.setText(work.page_name or work.artist.name if work else "")
            self.name_edit.setText(work.template_name or work.artist.name if work else "")
            self._refresh_table()
            self._refresh_albums()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status("改完点「保存并继续」；日期决定年份格子与排序")

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录」时由主窗口调）。"""
        self.work = None
        self._rows = []
        self._loading = True
        try:
            self.artist_label.setText("P主：—")
            self.page_edit.clear()
            self.name_edit.clear()
            self.table.setRowCount(0)
            self.album_list.clear()
        finally:
            self._loading = False
        self.preview.clear()
        self.count_label.clear()
        self.set_status("等新一轮生成…")

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        widgets.set_status_text(self.status_label, text, compact=(kind == "err"))

    # ------------------------------------------------------------ 表格
    def _refresh_table(self) -> None:
        """按 `self.work.songs` 重建表格（只在载入 / 增删 / 搜完名字后调）。"""
        songs = self.work.sorted_songs() if self.work else []
        self._rows = []
        self.table.setRowCount(0)
        for song in songs:
            self._append_row(song)

    def _append_row(self, song: pt.ProducerSong) -> None:
        row = self.table.rowCount()
        self._rows = [*self._rows, song]
        self.table.insertRow(row)
        for column, text in enumerate((song.year, song.cn, song.ja, song.date,
                                       "已建" if song.page_exists else "待建")):
            item = QtWidgets.QTableWidgetItem(str(text or ""))
            if column in (0, 4):                       # 年份、状态：只读
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                item.setForeground(QtGui.QBrush(QtGui.QColor("#72777d")))
            self.table.setItem(row, column, item)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        """格子改了就同步回模型，并刷新年份格与预览（不重建整张表，免得光标乱跳）。"""
        if self._loading or self.work is None:
            return
        row, column = item.row(), item.column()
        if row >= len(self._rows):
            return
        song = self._rows[row]
        text = item.text().strip()
        if column == 1:
            song.cn = text
        elif column == 2:
            song.ja = text
        elif column == 3:
            song.date = pt.normalise_date(text)
            self._loading = True
            try:
                self.table.item(row, 3).setText(song.date or text)
                self.table.item(row, 0).setText(song.year)
            finally:
                self._loading = False
        else:
            return
        self.work.songs = list(self._rows)
        self._update_preview()

    def _add_song(self) -> None:
        if self.work is None:
            return
        song = pt.ProducerSong(ja="", cn="", date="", source="手动")
        self.work.songs = [*self.work.sorted_songs(), song]
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        row = self.table.rowCount() - 1
        self.table.setCurrentCell(row, 1)
        self.table.editItem(self.table.item(row, 1))
        self._update_preview()
        self.set_status("新加的一行：填「中文条目 / 日文原名 / 投稿日期」，年份会自动算出来")

    def _remove_songs(self) -> None:
        if self.work is None:
            return
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self.set_status("先在表里选中要删的曲目", "warn")
            return
        for row in rows:
            if 0 <= row < len(self._rows):
                self._rows.pop(row)
        self.work.songs = list(self._rows)
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status(f"已删除 {len(rows)} 首", "ok")

    # ------------------------------------------------------------ 专辑
    def _refresh_albums(self) -> None:
        self.album_list.clear()
        for name in (self.work.albums if self.work else []):
            item = QtWidgets.QListWidgetItem(str(name))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
            self.album_list.addItem(item)

    def _collect_albums(self) -> None:
        if self.work is not None:
            self.work.albums = [self.album_list.item(row).text().strip()
                                for row in range(self.album_list.count())
                                if self.album_list.item(row).text().strip()]

    def _on_album_changed(self, _item: QtWidgets.QListWidgetItem) -> None:
        if self._loading:
            return
        self._collect_albums()
        self._update_preview()

    def _add_album(self) -> None:
        if self.work is None:
            return
        item = QtWidgets.QListWidgetItem("新专辑")
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        self.album_list.addItem(item)
        self.album_list.setCurrentItem(item)
        self.album_list.editItem(item)
        self._collect_albums()
        self._update_preview()

    def _remove_album(self) -> None:
        for item in self.album_list.selectedItems():
            self.album_list.takeItem(self.album_list.row(item))
        self._collect_albums()
        self._update_preview()

    # ------------------------------------------------------------ 其他按钮
    def _on_meta_changed(self) -> None:
        if self._loading or self.work is None:
            return
        self.work.page_name = self.page_edit.text().strip()
        self.work.template_name = self.name_edit.text().strip()
        self._update_preview()

    def _search_names(self) -> None:
        """逐首搜维基补中文条目名（后台跑，状态行报进度）。"""
        if self.work is None or self._busy():
            return
        pending = [song for song in self.work.songs if not song.cn and song.ja]
        if not pending:
            self.set_status("每首曲目都已经有中文条目名了", "ok")
            return
        self._set_busy(True)
        self.set_status(f"正在逐首搜索维基补条目名（{len(pending)} 首）…")
        progress = _Progress(lambda text: self.set_status(text))
        songs = self.work.songs
        self._run_background(lambda: pt.fill_missing_names(songs, progress.message.emit),
                             self._on_search_done)

    def _on_search_done(self, filled: Any) -> None:
        self._set_busy(False)
        if isinstance(filled, dict) and filled.get("ok") is False:
            self.set_status(f"搜索失败：{filled.get('error')}", "err")
            return
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status(f"补到 {filled} 个条目名" if filled else "维基上没搜到对应条目",
                        "ok" if filled else "warn")

    def _refresh_status(self) -> None:
        """重新批量查「已建 / 待建」。"""
        if self.work is None or self._busy():
            return
        songs = self.work.songs
        targets = list(dict.fromkeys(song.target() for song in songs if song.target()))
        if not targets:
            self.set_status("还没有曲目", "warn")
            return
        self._set_busy(True)
        self.set_status(f"正在查 {len(targets)} 个条目在不在…")

        def job() -> Dict[str, Any]:
            texts = wiki_api.fetch_pages_text(targets)
            for song in songs:
                song.page_exists = song.target() in texts
            return {"ok": True, "count": sum(1 for song in songs if song.page_exists)}

        self._run_background(job, self._on_status_done)

    def _on_status_done(self, result: Any) -> None:
        self._set_busy(False)
        if not isinstance(result, dict) or result.get("ok") is False:
            self.set_status(f"查询失败：{(result or {}).get('error')}", "err")
            return
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self.set_status(f"其中 {result.get('count')} 个条目已经建好", "ok")

    # ------------------------------------------------------------ 预览 / 保存
    def _update_preview(self) -> None:
        if self.work is None:
            self.preview.clear()
            return
        self.work.songs = self.work.sorted_songs()
        text = pt.build_template(self.work)
        self.preview.setPlainText(text)
        links = pt.template_links(text)
        self.count_label.setText(f"{len(self.work.songs)} 首曲目 · {len(self.work.albums)} 张专辑"
                                 f" · 模板里列了 {len(links)} 个条目")

    def _on_save(self) -> None:
        if self.work is None:
            return
        self._collect_albums()
        self.work.page_name = self.page_edit.text().strip() or self.work.artist.name
        self.work.template_name = self.name_edit.text().strip() or self.work.artist.name
        if not self.work.template_name:
            self.set_status("模板名不能为空", "err")
            return
        if not self.work.songs and not self.work.albums:
            self.set_status("既没有曲目也没有专辑，模板会是空的", "warn")
        self.saved.emit(self.work)

    # ------------------------------------------------------------ 后台
    def _busy(self) -> bool:
        return bool(self._workers)

    def _set_busy(self, busy: bool) -> None:
        for button in (self.save_button, self.cancel_button, self.search_button,
                       self.add_button, self.remove_button, self.refresh_button):
            button.setEnabled(not busy)
        self.table.setEnabled(not busy)

    def _run_background(self, func, callback) -> None:
        worker = FunctionWorker(func, parent=self)
        self._workers.append(worker)

        def handle(result: Any) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            callback(result)

        worker.done.connect(handle)
        worker.finished.connect(worker.deleteLater)
        worker.start()


__all__ = ["ProducerPanel", "COLUMNS"]
