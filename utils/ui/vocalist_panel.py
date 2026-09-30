"""「曲目」页（歌姬模板用）：把抓到的曲子按栏 / 站点 / 年份摆出来，可改可删、可人工复核。

这是侧栏第三个功能「生成歌姬模板」的第一页（用户 2026-09-30 要求的流程）：

    ┌ 歌姬：歌爱雪（引擎 VOCALOID · Template:歌爱雪）  210 首 · 3 条待复核 ┐
    ├ [人工复核待定项] [删除选中] [恢复原样]                              ┤
    ├ 栏 │ 站点 │ 年份 │ 条目名 │ 日文名 │ 备注（分栏说明 / 待复核原因）  ┤
    ├ 页面：Template:歌爱雪 + 17 个年份子页 + 文档                        ┤
    ├ 主模板 wikitext 预览（跟着上面的改动实时变，只读）                    ┤
    └ 状态行 ……………………………………… [取消] [保存并继续]                            ┘

三件事要记住：

* **栏 / 站点 / 年份都能改**（双击格子）：栏改的是「这一首算在哪个栏」（拆分了就看年份子页），
  站点写成「niconico、YouTube」这样用顿号分隔；年份决定它进哪个年份子页（取不到年份的
  会列进「待复核」）。条目名 / 日文名也可以改（改的是模板里那个链接）。
* **「人工复核待定项」**：拿不准的（殿堂页里查不到、条目里的荣誉题头跟殿堂页打架、
  连投稿年都取不到）一条一条弹窗让你改（用户 2026-09-30 要求）。改完写回表格。
* 预览只画**主模板**：真正的各年份子页在「提交」页里一页一页切换着看。
"""
import logging
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import vocalist_template as vt
from utils.ui import theme, widgets
from utils.ui.workers import FunctionWorker

COLUMNS = ("栏", "站点", "年份", "条目名", "日文名", "备注")
COLUMN_WIDTHS = (96, 150, 70, 200, 200, 260)
COLUMN_TIPS = (
    "这一首算在哪一栏：神话曲 / 传说曲 / 殿堂曲 / 破亿播放曲目 / 其他\n"
    "（同一首歌在不同站点可以各算各的，这里显示的是最高那一栏）",
    "它进哪几个站点格：niconico / YouTube / bilibili，用「、」分隔\n"
    "（改这里会把其它分栏合成这一栏）",
    "投稿年：决定它进哪个年份子页（拆分了才有用；取不到就留空，会被列进待复核）",
    "维基上的条目名（模板里链到这一页）",
    "日文原名（模板里写成 [[条目名|日文名]]）",
    "分栏说明（档次 / 来源）与需要人工复核的原因，只读",
)


def _split_list(text: str) -> List[str]:
    """「niconico、YouTube」→ `['niconico', 'YouTube']`（逗号 / 顿号 / 斜杠都当分隔）。"""
    raw = str(text or "").replace("，", "、").replace(",", "、").replace("/", "、")
    return [chunk.strip() for chunk in raw.split("、") if chunk.strip()]


class VocalistPanel(QtWidgets.QWidget):
    """曲目页。保存发 `saved(work)`，取消发 `cancelled`。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work: Optional[vt.VocalistWork] = None
        self._loading = False
        self._rows: List[vt.VocalistSong] = []
        self._workers: List[FunctionWorker] = []
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("歌姬：—", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1)
        self.template_label = QtWidgets.QLabel("", self)
        self.template_label.setStyleSheet(theme.quiet_label_style())
        top.addWidget(self.template_label)
        root.addLayout(top)

        tools = QtWidgets.QHBoxLayout()
        self.review_button = QtWidgets.QPushButton("人工复核待定项", self)
        self.review_button.setToolTip(
            "把拿不准的曲子一条一条摆出来：殿堂页里没查到、条目里的荣誉题头跟殿堂页"
            "对不上、取不到投稿年 —— 改完写回表格（用户 2026-09-30 要求）")
        self.review_button.clicked.connect(self._review_flags)
        tools.addWidget(self.review_button)
        self.remove_button = QtWidgets.QPushButton("删除选中", self)
        self.remove_button.setToolTip("这几首不写进模板（比如不是这位歌姬主唱的歌）")
        self.remove_button.clicked.connect(self._remove_songs)
        tools.addWidget(self.remove_button)
        self.restore_button = QtWidgets.QPushButton("恢复原样", self)
        self.restore_button.setToolTip("撤销表格里的改动，回到刚抓下来的样子")
        self.restore_button.clicked.connect(self._restore)
        tools.addWidget(self.restore_button)
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
        self.table.setMinimumHeight(260)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 3)

        pages_head = QtWidgets.QHBoxLayout()
        pages_head.addWidget(QtWidgets.QLabel("要生成的页面", self))
        self.pages_label = QtWidgets.QLabel("", self)
        self.pages_label.setStyleSheet(theme.quiet_label_style())
        self.pages_label.setWordWrap(True)
        pages_head.addWidget(self.pages_label, 1)
        root.addLayout(pages_head)

        preview_head = QtWidgets.QHBoxLayout()
        preview_head.addWidget(QtWidgets.QLabel("主模板 wikitext（实时变，只读）", self))
        self.count_label = QtWidgets.QLabel("", self)
        self.count_label.setStyleSheet(theme.quiet_label_style())
        preview_head.addStretch(1)
        preview_head.addWidget(self.count_label)
        root.addLayout(preview_head)

        self.preview = QtWidgets.QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(180)
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
        """打开这一页：`payload["work"]` 是 `VocalistWork`。"""
        work = (payload or {}).get("work")
        self.work = work
        if work is not None and not getattr(self, "_original", None):
            self._original = work.copy()
        self._loading = True
        try:
            self.title_label.setText(
                (f"歌姬：{work.name}（引擎 {work.engine} · "
                 f"Template:{work.name}{'/年份' if work.split else ''}）") if work else "歌姬：—")
            self.template_label.setText(work.summary if work else "")
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.review_button.setEnabled(bool(work and work.flags))
        self.set_status("栏 / 站点 / 年份都能双击改；拿不准的点「人工复核待定项」")

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录」时由主窗口调）。"""
        self.work = None
        self._original = None
        self._rows = []
        self._loading = True
        try:
            self.title_label.setText("歌姬：—")
            self.template_label.clear()
            self.table.setRowCount(0)
        finally:
            self._loading = False
        self.preview.clear()
        self.pages_label.clear()
        self.count_label.clear()
        self.set_status("等新一轮生成…")

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        widgets.set_status_text(self.status_label, text, compact=(kind == "err"))

    # ------------------------------------------------------------ 表格
    def _refresh_table(self) -> None:
        self._rows = list(self.work.songs) if self.work else []
        self.table.setRowCount(0)
        for song in self._rows:
            self._append_row(song)

    def _append_row(self, song: vt.VocalistSong) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (song.rank, "、".join(song.stations), song.year, song.title, song.ja,
                  self._note_of(song))
        for column, text in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(text or ""))
            if column == 5:                           # 备注只读
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                item.setForeground(QtGui.QBrush(QtGui.QColor("#72777d")))
            if song.flag:
                item.setToolTip(song.flag)
            self.table.setItem(row, column, item)

    @staticmethod
    def _note_of(song: vt.VocalistSong) -> str:
        parts = [part for part in (song.places_text(), song.source, song.flag) if part]
        return " · ".join(parts)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        """格子改了就同步回模型（不重建整张表，免得光标乱跳）。"""
        if self._loading or self.work is None:
            return
        row, column = item.row(), item.column()
        if row >= len(self._rows) or column == 5:
            return
        song = self._rows[row]
        text = item.text().strip()
        if column == 0:
            song.set_rank(text)
        elif column == 1:
            song.set_stations(_split_list(text))
        elif column == 2:
            song.year = text
        elif column == 3:
            song.title = text
        elif column == 4:
            song.ja = text
        self._loading = True
        try:
            self.table.item(row, 5).setText(self._note_of(song))
        finally:
            self._loading = False
        self._update_preview()

    def _remove_songs(self) -> None:
        if self.work is None:
            return
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self.set_status("先在表里选中要删的曲子", "warn")
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
        self.set_status(f"已删掉 {len(rows)} 首（点「恢复原样」可以撤销）", "ok")

    def _restore(self) -> None:
        """撤销表格改动，回到刚抓下来的样子。"""
        if self.work is None or not getattr(self, "_original", None):
            return
        keep = {"styles", "relation", "existing", "existing_doc"}
        restored = self._original.copy()
        for key in keep:
            setattr(restored, key, getattr(self.work, key))
        self.work.songs = restored.songs
        self.work.flags = restored.flags
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status("已恢复成刚抓下来的样子", "ok")

    # ------------------------------------------------------------ 人工复核
    def _review_flags(self) -> None:
        """把待复核的曲子一条一条摆出来让用户定（用户 2026-09-30 要求）。"""
        if self.work is None or not self.work.flags:
            self.set_status("没有需要复核的曲子", "ok")
            return
        items = list(self.work.flags)
        fixed = skipped = 0
        for index, flag in enumerate(items, start=1):
            song = next((item for item in self.work.songs if item.title == flag.get("title")),
                        None)
            if song is None:
                continue
            choice, values = self._review_song(index, len(items), song, flag.get("reason") or "")
            if choice == "stop":
                break
            if choice != "accept":
                skipped += 1
                continue
            rank, stations, year = values
            if rank and rank != song.rank:
                song.set_rank(rank)
            if stations:
                song.set_stations(stations)
            if year:
                song.year = year
            song.flag = ""
            fixed += 1
        self.work.flags = []
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.review_button.setEnabled(False)
        message = f"已处理 {fixed} 条待复核"
        if skipped:
            message += f"，跳过 {skipped} 条"
        self.set_status(message, "ok" if fixed else "warn")

    def _review_song(self, index: int, total: int, song: vt.VocalistSong, reason: str
                     ) -> tuple:
        """复核一条 → `(选择, (栏, 站点, 年份))`，选择是 accept / skip / stop。"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"人工复核（{index}/{total}）")
        dialog.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(dialog)
        title = QtWidgets.QLabel(f"{song.title}（{song.ja or '无日文名'}）", dialog)
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        title.setFont(font)
        title.setWordWrap(True)
        layout.addWidget(title)
        note = QtWidgets.QLabel("为什么要复核：" + (reason or "看不太准"), dialog)
        note.setWordWrap(True)
        note.setStyleSheet(theme.quiet_label_style())
        layout.addWidget(note)
        source = QtWidgets.QLabel(
            f"现在的判断：{song.rank} / {'、'.join(song.stations) or '（没站点）'} / "
            f"{song.year or '（没年份）'}（{song.source or '来源不明'}）", dialog)
        source.setWordWrap(True)
        layout.addWidget(source)

        form = QtWidgets.QFormLayout()
        rank_edit = QtWidgets.QComboBox(dialog)
        rank_edit.setEditable(True)
        rank_edit.addItems([vt.RANK_MYTH, vt.RANK_LEGEND, vt.RANK_HALL, vt.RANK_BILLION,
                            vt.RANK_OTHER])
        rank_edit.setCurrentText(song.rank)
        form.addRow("栏", rank_edit)
        station_edit = QtWidgets.QLineEdit("、".join(song.stations), dialog)
        station_edit.setPlaceholderText("niconico、YouTube、bilibili（用、分隔）")
        form.addRow("站点", station_edit)
        year_edit = QtWidgets.QLineEdit(song.year, dialog)
        year_edit.setPlaceholderText("投稿年，例如 2024")
        form.addRow("年份", year_edit)
        layout.addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        accept = QtWidgets.QPushButton("采用并下一个", dialog)
        accept.setDefault(True)
        theme.mark_accent(accept)
        skip = QtWidgets.QPushButton("跳过", dialog)
        stop = QtWidgets.QPushButton("剩下的都跳过", dialog)
        for button in (accept, skip, stop):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        choice = {"value": "skip"}                  # 直接关窗口也算跳过
        accept.clicked.connect(lambda: (choice.update(value="accept"), dialog.accept()))
        skip.clicked.connect(dialog.reject)
        stop.clicked.connect(lambda: (choice.update(value="stop"), dialog.reject()))
        dialog.exec_()
        if choice["value"] != "accept":
            return choice["value"], ("", [], "")
        return "accept", (rank_edit.currentText().strip(), _split_list(station_edit.text()),
                          year_edit.text().strip())

    # ------------------------------------------------------------ 预览 / 保存
    def _update_preview(self) -> None:
        if self.work is None:
            self.preview.clear()
            self.pages_label.clear()
            return
        self.preview.setPlainText(vt.build_main_template(self.work))
        specs = vt.page_specs(self.work)
        self.pages_label.setText(" → ".join(spec["name"] for spec in specs[:8])
                                 + (" …" if len(specs) > 8 else ""))
        counts = vt.rank_counts(self.work)
        detail = "、".join(f"{rank} {count}" for rank, count in counts.items())
        self.count_label.setText(f"{len(self.work.songs)} 首（{detail}）· "
                                 f"{len(specs)} 个页面"
                                 + (f" · {len(self.work.flags)} 条待复核"
                                    if self.work.flags else ""))

    def _on_save(self) -> None:
        if self.work is None:
            return
        if not self.work.songs:
            self.set_status("一首曲子都没有，模板会是空的", "warn")
        if self.work.flags:
            self.set_status(f"还有 {len(self.work.flags)} 条没复核（可以点「人工复核待定项」，"
                            "也可以就这么继续）", "warn")
        unknown = self.work.yearless()
        if self.work.split and unknown:
            logging.warning("有 %d 首曲子没有年份，拆分成子页时它们不会被写进任何年份页：%s",
                            len(unknown), "、".join(song.title for song in unknown[:10]))
            self.set_status(f"⚠️ 有 {len(unknown)} 首取不到投稿年，拆分的年份子页里不会出现"
                            "它们（建议先复核、或在表格里补上年份）", "warn")
        self.saved.emit(self.work)


__all__ = ["VocalistPanel", "COLUMNS"]
