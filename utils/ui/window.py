"""PyQt5 主窗口：把原本在终端里做的问答搬进界面。

窗口结构（参考 MediaWiki 的 Timeless 皮肤）：

    ┌────┬────────────────────────────────────┐
    │ 侧 │ 标签页：填写信息 / 日志 / 样式 / …    │
    │ 栏 │  （内容区，白底卡片 + 顶部 wiki-tabs） │
    │    │                                    │
    │头像│                                    │
    │齿轮│ 状态条：当前在干什么 / 出错提示       │
    └────┴────────────────────────────────────┘

- **左侧竖栏**（`utils/ui/sidebar.py`）：切换不同功能（目前只有「生成歌曲条目」，
  为以后的功能留位置），底部是头像（点击登录 Vocawiki，登录后显示本人头像）
  与设置齿轮。具体外观见 `utils/ui/theme.py`。
- 「填写信息」页：上方是对话记录（问过什么、答了什么），下面是当前问题的输入区。
  整个生成流程跑在后台线程里，跑到需要提问时就停下来，等问题在这里被回答。
- 「日志」页：logging 与 print 的输出（网络请求、上传进度、错误都出现在这里）。
- 「样式」/「歌词」/「提交」页：原来的三个 html 窗口，现在是同一个窗口里的标签页，
  流程走到哪一步就把哪一页点亮（见 utils/ui/style_panel.py、lyrics_panel.py、submit_panel.py）。
  这几页外面都套了一层滚动区（`MainWindow._scrollable_page`）：它们的自然宽度都在 1000px 以上，
  直接挂在标签栏上会把主窗口的最小宽度顶到 1300+，窗口就拖不窄了；套上滚动区之后
  窗口可以随便拖，实在不够宽时内容横向滚动。

线程模型：只有主线程碰控件。工作线程要提问时通过 `_Bridge.asked` 信号把
`PromptRequest` 丢过来，然后阻塞在自己的 Event 上；用户答完由主线程 set 结果。
同样地，日志（可能在任意线程产生）与 print 输出都经信号切回主线程再写控件。
"""
import logging
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import login
from utils.string import is_empty
from utils.ui import avatar as avatar_lib
from utils.ui import icons, theme, widgets
from utils.ui.sidebar import SideBar
from utils.ui.workers import FunctionWorker

APP_TITLE = "Vocawiki 条目辅助工具"

# 窗口图标文件名（build.py 把 icon.ico 打进包内 assets/，两边保持一致）
_ICON_NAMES = ("icon.ico", "icon.png")

# 编辑器标签页：key -> (标题, 说明)
PANEL_TITLES = {
    "style": ("样式", "颜色 / 字体 / 边框 / 渐变（原 html/css-tag-editor.html）"),
    "lyrics": ("歌词", "把混在一起的歌词拆成三栏（原 html/lyrics-editor.html）"),
    "submit": ("提交", "预览并提交到 Vocawiki（原 html/wikitext-editor.html）"),
}


# ---------------------------------------------------------------- 请求与桥

@dataclass
class PromptRequest:
    """工作线程 → 主线程的一次请求（提问或「把某个编辑器页点亮」）。"""

    kind: str                                  # response / choices / multiline / panel
    prompt: str = ""
    choices: List[str] = field(default_factory=list)
    allow_zero: bool = False
    auto_strip: bool = True
    checker: Callable[[str], bool] = None
    panel: str = ""                             # kind == panel 时的页名
    payload: Any = None
    value: Any = None
    error: Optional[str] = None
    cancelled: bool = False
    event: threading.Event = field(default_factory=threading.Event)

    def done(self, value: Any = None, error: Optional[str] = None,
             cancelled: bool = False) -> None:
        self.value, self.error, self.cancelled = value, error, cancelled
        self.event.set()

    def wait(self) -> Any:
        self.event.wait()
        if self.error:
            raise RuntimeError(self.error)
        # 这一轮被放弃了（「清除对话记录」）：流程应当尽快结束，别再往回走
        from utils import ui as ui_facade          # 延迟导入，别把门面拖进循环依赖
        ui_facade.check_run_cancelled()
        return self.value


class _Bridge(QtCore.QObject):
    """跨线程信号集中营（Qt 会把它们排队到主线程执行）。"""

    asked = QtCore.pyqtSignal(object)           # PromptRequest
    output = QtCore.pyqtSignal(str, bool)       # 文本, 是否 stderr
    log = QtCore.pyqtSignal(str, str)           # 文本, levelname
    status = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal(object)            # flow 的返回值
    failed = QtCore.pyqtSignal(str, str)        # 简短信息, 完整堆栈
    crashed = QtCore.pyqtSignal(str, str)       # 界面回调崩了（不结束流程）


class StreamWriter:
    """替换 sys.stdout / sys.stderr，把 print 的输出也导到「日志」页。"""

    def __init__(self, bridge: _Bridge, is_error: bool = False):
        self._bridge = bridge
        self._is_error = is_error

    def write(self, text: str) -> int:
        text = text if isinstance(text, str) else str(text)
        if text:
            self._bridge.output.emit(text, self._is_error)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self):
        raise OSError("界面里的输出流没有文件描述符")


class QtLogHandler(logging.Handler):
    """把 logging 记录转发到「日志」页（emit 可能发生在工作线程，靠信号切回主线程）。"""

    def __init__(self, bridge: _Bridge, level: int = logging.NOTSET):
        super().__init__(level)
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.log.emit(self.format(record), record.levelname)
        except Exception:                     # 记录日志本身绝不能炸掉流程
            pass


def install_stream_capture(bridge: _Bridge) -> None:
    """把 print 与 stderr 导到界面（打包成 --windowed 后 sys.stdout 本来就是 None）。"""
    sys.stdout = StreamWriter(bridge, is_error=False)
    sys.stderr = StreamWriter(bridge, is_error=True)


# ---------------------------------------------------------------- 「填写信息」页

class PromptTab(QtWidgets.QWidget):
    """对话记录 + 当前问题输入区。所有控件都只在主线程里动。"""

    restart_requested = QtCore.pyqtSignal()     # 「清除对话记录」清完后，想从头再来一轮

    def __init__(self, parent=None):
        super().__init__(parent)
        self._request: Optional[PromptRequest] = None
        layout = QtWidgets.QVBoxLayout(self)
        # 左右不留边：内容框的框线要与上面标签页的左边界对齐（见 theme 里 QTabWidget::pane 的说明）
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(5)

        self.history = QtWidgets.QTextBrowser(self)
        self.history.setOpenExternalLinks(True)
        # 对话记录占的地方最大、要一直看：比正文再大一号；字号跟着窗口缩放（theme.scale_font）
        theme.scale_font(self.history, theme.HISTORY_FONT_PX)
        # 文档自带的 4px 边距会再把文字推右一点，去掉它，改由内部留白对齐
        self.history.document().setDocumentMargin(0)
        self.history.setStyleSheet(
            f"QTextBrowser {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; "
            f"padding: 4px {theme.CONTENT_PAD_PX}px; }}")
        layout.addWidget(self.history, 1)

        self.question = QtWidgets.QLabel("准备中…", self)
        self.question.setWordWrap(True)
        # 问句比正文大一号、加粗；句子本身贴左边，与下面的输入框外框对齐（缩进会让它看起来缩进去一截）
        theme.scale_font(self.question, theme.QUESTION_FONT_PX, bold=True)
        self.question.setIndent(0)
        layout.addWidget(self.question)

        self.stack = QtWidgets.QStackedWidget(self)
        # 输入区不抢高度：高出来的部分都给上面的对话记录
        self.stack.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                 QtWidgets.QSizePolicy.Maximum)
        layout.addWidget(self.stack)
        self.text_page = self._build_text_page()
        self.multiline_page = self._build_multiline_page()
        self.choices_page = self._build_choices_page()
        self.stack.addWidget(self.text_page)
        self.stack.addWidget(self.multiline_page)
        self.stack.addWidget(self.choices_page)

        self.hint = QtWidgets.QLabel("", self)
        self.hint.setStyleSheet(theme.color_style(theme.DANGER))
        self.hint.setWordWrap(True)
        self.hint.setIndent(0)
        self.hint.setVisible(False)              # 没提示时整行收起来，别白占一块高度
        layout.addWidget(self.hint)

        # Ctrl+L 清空当前输入（快捷键只在本页内生效，不会抢别的面板）
        self.clear_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+L"), self)
        self.clear_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.clear_shortcut.activated.connect(self.clear_input)
        self.set_busy(False)

    # —— 三种输入区的搭建 ——
    def _build_clear_button(self, parent: QtWidgets.QWidget) -> QtWidgets.QPushButton:
        """「清空」：接在当前输入行里，不管在哪个输入页都调同一个 clear_input。"""
        button = QtWidgets.QPushButton("清空", parent)
        button.setToolTip("清空输入框里的内容（Ctrl+L），不会动上面的对话记录")
        button.clicked.connect(self.clear_input)
        return button

    def _build_history_button(self, parent: QtWidgets.QWidget) -> QtWidgets.QPushButton:
        """「清除对话记录」：红按钮，接在「确定 / 完成」左边（确认弹窗可在设置里关掉）。"""
        button = QtWidgets.QPushButton("清除对话记录", parent)
        button.setToolTip("把上面的对话记录清空；「设置」页可以关掉确认弹窗")
        theme.mark_danger(button)
        button.clicked.connect(self.clear_history)
        return button

    def _build_text_page(self) -> QtWidgets.QWidget:
        """单行输入：输入框独占一整行，三颗按钮靠右下（与多行页一致）。"""
        page = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.text_input = QtWidgets.QLineEdit(page)
        self.text_input.setPlaceholderText("在这里输入后按回车")
        self.text_input.returnPressed.connect(self.submit_text)
        column.addWidget(self.text_input)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)                          # 按钮统统贴右边
        self.text_clear_button = self._build_clear_button(page)
        row.addWidget(self.text_clear_button)
        self.text_button = QtWidgets.QPushButton("确定", page)
        self.text_button.setDefault(True)
        self.text_button.clicked.connect(self.submit_text)
        row.addWidget(self.text_button)
        self.text_history_button = self._build_history_button(page)
        row.addWidget(self.text_history_button)
        column.addLayout(row)
        # 多行页比单行页高，QStackedWidget 会按最高的那页给空间：末尾留一个 stretch，
        # 多出来的高度就全落在下面，按钮不会被拉开一大截空隙（否则输入框和按钮之间会空 30px+）
        column.addStretch(1)
        return page

    def _build_multiline_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.multiline_input = QtWidgets.QPlainTextEdit(page)
        self.multiline_input.setPlaceholderText("整段粘贴到这里，然后点「完成」")
        # 刻意压扁：输入区不再往下长，要看全文就在框里滚
        self.multiline_input.setMinimumHeight(68)
        self.multiline_input.setMaximumHeight(84)
        column.addWidget(self.multiline_input)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)
        self.multiline_clear_button = self._build_clear_button(page)
        row.addWidget(self.multiline_clear_button)
        self.multiline_button = QtWidgets.QPushButton("完成", page)
        self.multiline_button.setDefault(True)
        self.multiline_button.clicked.connect(self.submit_multiline)
        row.addWidget(self.multiline_button)
        self.multiline_history_button = self._build_history_button(page)
        row.addWidget(self.multiline_history_button)
        column.addLayout(row)
        return page

    def _build_choices_page(self) -> QtWidgets.QWidget:
        """选择题：选项按钮两列排。

        套一层滚动区：版本列表（「其他版本」候选）能有三四十项，全摊开会把窗口顶得很高，
        超过 220px 就让它自己滚。
        这里以前还有一颗「取消本次选择」按钮，但它接了 _choose(0)：0 只对 allow_zero
        的提问有意义，流程却都按「1 = 第一个选项」判断（0 会被当成另一个分支，
        比如「否」或「是」），点一下就可能静默走错路——也没人会看见它，直接删掉。
        """
        self.choices_scroll = QtWidgets.QScrollArea(self)
        self.choices_scroll.setObjectName("choicesScroll")
        self.choices_scroll.setWidgetResizable(True)
        self.choices_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.choices_scroll.setMaximumHeight(220)
        inner = QtWidgets.QWidget(self.choices_scroll)
        self.choices_layout = QtWidgets.QGridLayout(inner)
        self.choices_layout.setContentsMargins(0, 0, 6, 0)
        self.choices_scroll.setWidget(inner)
        return self.choices_scroll

    # —— 界面状态 ——
    def finish(self, note: str = "") -> None:
        """流程结束（跑完 / 出错）：输入区收回单行页。

        红色的「清除对话记录」只挂在单行 / 多行输入页上；而流程最后常常问的是**选择题**
        （要不要上传封面、人声本家、投稿文…），跑完停在那页的话，界面上就找不到这颗按钮了
        （用户 2026-09 反馈）。所以统一回到单行页，并把当时那句问题换成收尾提示。
        """
        self._request = None
        self.set_busy(False)                    # 输入区变灰：流程已经结束，不再等回答
        self._clear_choices()                   # 上一次的选项按钮是旧的，别留着
        self.stack.setCurrentIndex(0)
        self.text_input.clear()
        self.multiline_input.clear()
        self._set_hint("")
        self.question.setText(note or "生成流程已结束")

    def set_busy(self, busy: bool) -> None:
        """busy=True 表示「正等你回答」；输入区跟着开关，但清空 / 清除对话记录随时能用。"""
        self.question.setEnabled(busy)
        self.text_input.setEnabled(busy)
        self.text_button.setEnabled(busy)
        self.text_clear_button.setEnabled(busy)
        self.multiline_input.setEnabled(busy)
        self.multiline_button.setEnabled(busy)
        self.multiline_clear_button.setEnabled(busy)
        self.choices_page.setEnabled(busy)

    def reset(self) -> None:
        """恢复成刚打开界面时的样子（「清除对话记录 → 从头开始」时调）。

        对话记录、输入框、选项按钮、底部提示全清，问题那句回到初始的「准备中…」。
        """
        self._request = None
        self.set_busy(False)
        self._clear_choices()
        self.stack.setCurrentIndex(0)
        self.history.clear()
        self.text_input.clear()
        self.multiline_input.clear()
        self._set_hint("")
        self.question.setText("准备中…")

    def cancel_request(self) -> None:
        """放下悬着的那个提问（这一轮被放弃了）：等着的 `wait()` 会醒过来。"""
        request = self._request
        self._request = None
        if request is not None:
            request.done(cancelled=True)

    def clear_input(self) -> None:
        """清空当前输入区（只动输入框，上面问过什么的对话记录保留）。"""
        self.text_input.clear()
        self.multiline_input.clear()
        self._set_hint("")
        if self.stack.currentIndex() == 1:
            self.multiline_input.setFocus()
        else:
            self.text_input.setFocus()

    def _set_hint(self, text: str) -> None:
        """底部那行红字提示：没内容时整行收起来，不白占一块高度。"""
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def clear_history(self) -> bool:
        """清除上面的对话记录（清不清得问一句由设置决定）。返回是否真的清了。

        清完还会发 `restart_requested`：主窗口在这一轮已经结束时会把各页清干净、
        重新跑一遍生成流程（重新问「歌名？」），见 `MainWindow._restart_flow`。
        """
        if not self._confirm_clear_history():
            return False
        self.history.clear()
        self.restart_requested.emit()
        return True

    def _confirm_clear_history(self) -> bool:
        """要不要先弹窗问一句：看 config 的 confirm_clear_history（默认问）。"""
        try:
            from config.config import get_config
            if not get_config().confirm_clear_history:
                return True
        except Exception as e:                       # noqa: BLE001 - 读不到配置就当要问
            logging.debug("读取 confirm_clear_history 失败，仍然弹窗确认：%s", e)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("清除对话记录")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText("确定要清除上面的对话记录吗？")
        box.setInformativeText("问过什么、答了什么都一起消失，不能恢复；"
                               "这一轮已经结束时，清除后会从「歌曲原名」重新问起。")
        clear_button = box.addButton("清除", QtWidgets.QMessageBox.DestructiveRole)
        cancel_button = box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(cancel_button)          # 默认「取消」，免得手滑清掉
        box.exec_()
        return box.clickedButton() is clear_button

    def append_history(self, text: str) -> None:
        self.history.append(text)
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # —— 主线程收到请求 ——
    def start_request(self, request: PromptRequest) -> None:
        self._request = request
        self._set_hint("")
        self.question.setText(request.prompt)
        self.set_busy(True)
        if request.kind == "response":
            self.stack.setCurrentIndex(0)
            self.text_input.clear()
            self.text_input.setFocus()
        elif request.kind == "multiline":
            self.stack.setCurrentIndex(1)
            self.multiline_input.clear()
            self.multiline_input.setFocus()
        else:
            self.stack.setCurrentIndex(2)
            self._fill_choices(request)
        self.append_history(f"<b>{self._escape(request.prompt)}</b>")

    def _clear_choices(self) -> None:
        """把上一次摆出来的选项按钮清掉（重新出题 / 流程结束时都要）。"""
        while self.choices_layout.count():
            item = self.choices_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _fill_choices(self, request: PromptRequest) -> None:
        self._clear_choices()
        # 编号跟终端一致：真选项从 1 起（以前从 0 起，导致点第一颗按钮返回 0，
        # 流程把 0 当成「否」——于是点「是」反而不弹歌词窗口）；allow_zero 时额外给一个 0。
        options = [(0, "都不要（留空）")] if request.allow_zero else []
        options += list(enumerate(request.choices, start=1))
        for slot, (value, choice) in enumerate(options):
            button = QtWidgets.QPushButton(f"{value}. {choice}", self)
            button.clicked.connect(lambda _checked, answer=value: self._choose(answer))
            self.choices_layout.addWidget(button, slot // 2, slot % 2)

    # —— 提交答案 ——
    def submit_text(self) -> None:
        request = self._request
        if request is None:
            return
        value = self.text_input.text()
        if request.auto_strip:
            value = value.strip()
        checker = request.checker
        if checker is not None and not checker(value):
            self._set_hint(f"「{value}」不符合要求，请重新输入。")
            return
        self._finish(value)

    def submit_multiline(self) -> None:
        request = self._request
        if request is None:
            return
        lines: List[str] = []
        for line in self.multiline_input.toPlainText().replace("\r\n", "\n").split("\n"):
            if is_empty(line):
                break                       # 与终端里「空行结束」一致
            lines.append(line.strip() if request.auto_strip else line)
        if not lines:
            self._set_hint("还没有内容，粘贴后再点「完成」。")
            return
        self._finish(lines)

    def _choose(self, value: int) -> None:
        request = self._request
        if request is None:
            return
        if value == 0 and not request.allow_zero:
            # 没有「都不要」这项时，0 不是合法答案：流程按 1 起编号判断，
            # 递个 0 过去它就会当成别的分支（「否」/「是」）——宁可不当答案。
            return
        label = request.choices[value - 1] if value > 0 else "（都不要）"
        self._finish(value, label)

    def _finish(self, value: Any, label: Optional[str] = None) -> None:
        request = self._request
        self._request = None
        self.set_busy(False)
        shown = value if label is None else label
        if isinstance(value, list):
            shown = f"（{len(value)} 行）" + "".join(f"<br/>{self._escape(line)}" for line in value[:40])
        self.append_history(f"<span style='color:#14866d'>{self._escape(str(shown))}</span>")
        if request is not None:
            request.done(value)

    @staticmethod
    def _escape(text: str) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br/>"))


# ---------------------------------------------------------------- 「日志」页

class LogTab(QtWidgets.QWidget):
    """logging / print 的输出，附「打开输出文件夹」「复制」「清空」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_debug = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        row = QtWidgets.QHBoxLayout()
        self.debug_check = QtWidgets.QCheckBox("显示调试日志", self)
        self.debug_check.toggled.connect(self._on_debug_toggled)
        row.addWidget(self.debug_check)
        row.addStretch(1)
        self.copy_button = QtWidgets.QPushButton("复制全部", self)
        self.copy_button.clicked.connect(self.copy_all)
        row.addWidget(self.copy_button)
        self.clear_button = QtWidgets.QPushButton("清空", self)
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.view = QtWidgets.QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        theme.scale_font(self.view, theme.MONO_SIZE_PX, mono=True)
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG}; "
            f"border: 1px solid {theme.BORDER}; }}")
        layout.addWidget(self.view, 1)

    def _on_debug_toggled(self, checked: bool) -> None:
        self.show_debug = checked

    def append(self, text: str, level: str = "") -> None:
        if level == "DEBUG" and not self.show_debug:
            return
        for line in str(text).splitlines() or [""]:
            self.view.appendPlainText(line)
        scrollbar = self.view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        """清空日志（「清除对话记录 → 从头开始」时也要把上一轮的输出洗掉）。"""
        self.view.clear()

    def copy_all(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.view.toPlainText())


# ---------------------------------------------------------------- 主窗口

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, title: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.bridge = _Bridge()
        self.setWindowTitle(title or APP_TITLE)
        self.resize(1260, 880)
        # 窗口得能任人拖窄（用户 2026-09 反馈「拖动改宽度失败」）：内容页各自带滚动条，
        # 所以这里显式给小一点的下限——不写的话 Qt 会把各页的最小宽度加起来当成下限
        self.setMinimumSize(720, 520)
        self._finished = False
        self._panels = {}
        self._panel_requests = {}
        self._settings_return = None            # 进「设置」页之前在哪一页（关掉时回去）
        self._avatar_state = (False, "")        # (已登录, 用户名)：用来判断要不要刷新头像
        self._avatar_image = None               # 下载到的真实头像字节（可能为 None）
        self._login_thread = None
        self._logout_thread = None
        # 生成流程当前是不是在跑（「清除对话记录」要不要顺带重来一轮看它）；
        # `_start_flow` 由 `launch()` 挂上来，单测里手搓的窗口就是 None
        self._flow_running = False
        self._start_flow: Optional[Callable[[], None]] = None
        # 字号缩放用窗口自己的定时器推迟到本轮事件处理完再算：
        # 直接在 resizeEvent 里重套样式表会边重排边再触发 resize（拖窗口时也连着重排）
        self._scale_timer = QtCore.QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(0)
        self._scale_timer.timeout.connect(self._apply_font_scale)
        self._build_ui()
        self._connect()
        self._start_avatar_watch()

    # —— 搭建 ——
    def _build_ui(self) -> None:
        # 内容区：原来是「一个 QTabWidget 走天下」，现在外面套一层功能页
        # （QStackedWidget），侧栏选功能、标签页选这个功能里的哪一页。
        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.prompt_tab = PromptTab(self)
        self.log_tab = LogTab(self)
        self.tabs.addTab(self.prompt_tab, "填写信息")
        self.tabs.addTab(self.log_tab, "日志")
        self._add_panels()

        workflow = QtWidgets.QWidget(self)
        workflow_layout = QtWidgets.QVBoxLayout(workflow)
        workflow_layout.setContentsMargins(12, 8, 12, 0)
        workflow_layout.setSpacing(0)
        workflow_layout.addWidget(self.tabs, 1)

        self.feature_stack = QtWidgets.QStackedWidget(self)
        self.feature_stack.addWidget(workflow)
        self._feature_pages = {"entry": workflow}

        self.sidebar = SideBar(self, brand=self._brand_pixmap())
        self.sidebar.feature_selected.connect(self._on_feature_selected)
        self.sidebar.settings_requested.connect(self.show_settings)
        self.sidebar.avatar_clicked.connect(self._on_avatar_clicked)
        # 齿轮就是侧栏底部那个按钮；保留 settings_button 这个名字方便别处引用
        self.settings_button = self.sidebar.settings_button
        self.avatar_button = self.sidebar.avatar_button

        right = QtWidgets.QWidget(self)
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.feature_stack, 1)
        right_layout.addWidget(self._build_status_strip(right))

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(right, 1)
        self.setCentralWidget(central)
        self.sidebar.set_current_feature("entry")

    def _brand_pixmap(self) -> Optional[QtGui.QPixmap]:
        """侧栏顶部的品牌块：有程序图标就用程序图标，否则用画出来的色块。"""
        icon = _app_icon()
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(30, 30)
            if not pixmap.isNull():
                return pixmap
        return icons.brand_pixmap(30)

    def _build_status_strip(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """底部一条状态栏：当前在干什么 / 出错提示（不用 QStatusBar，免得不稳）。"""
        bar = QtWidgets.QWidget(parent)
        bar.setObjectName("statusStrip")
        bar.setStyleSheet(f"QWidget#statusStrip {{ background: {theme.BG_PAGE}; "
                          f"border-top: 1px solid {theme.BORDER}; }}")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 5, 12, 5)
        row.setSpacing(8)
        self.status_label = QtWidgets.QLabel("正在启动…", bar)
        self.status_label.setStyleSheet(theme.quiet_label_style())
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        return bar

    def _add_panels(self) -> None:
        """三个编辑器页先建好但不可点，流程走到哪一步再点亮哪一页；设置页平时藏起来。

        每一页都先套一层滚动区（见 `_scrollable_page`）：这些页的最小宽度（长提示文字 +
        多栏歌词 + 预览台）都在 1000px 以上，直接当标签页会把主窗口的最小宽度顶到 1300+，
        窗口就拖不窄了（用户 2026-09 反馈「拖动改变窗口宽度失败」）。
        """
        from utils.ui.lyrics_panel import LyricsPanel
        from utils.ui.settings_panel import SettingsPanel
        from utils.ui.style_panel import StylePanel
        from utils.ui.submit_panel import SubmitPanel
        panels = {"style": StylePanel(self), "lyrics": LyricsPanel(self),
                  "submit": SubmitPanel(self)}
        self._page_areas = {}
        for key, (label, tooltip) in PANEL_TITLES.items():
            panel = panels[key]
            panel.setToolTip(tooltip)
            area = self._scrollable_page(panel)
            self._page_areas[key] = area
            index = self.tabs.addTab(area, label)
            self.tabs.setTabEnabled(index, False)
            self._panels[key] = panel
        self.panels = panels
        self.settings_panel = SettingsPanel(self)
        self.settings_area = self._scrollable_page(self.settings_panel)
        self._page_areas["settings"] = self.settings_area
        index = self.tabs.addTab(self.settings_area, "设置")
        self.tabs.setTabToolTip(index, "可视化修改配置与账号 / 密钥（侧栏底部的齿轮也是这里）")
        # 「设置」页不算工作流里的页：默认不挂在标签栏上，点侧栏齿轮才叫出来（见 show_settings）
        self.tabs.setTabVisible(index, False)

    def _scrollable_page(self, panel: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        """给一页套上滚动区：窗口拖窄时内容不会被裁掉，只会多一条滚动条。"""
        area = QtWidgets.QScrollArea(self)
        area.setObjectName("pageScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setWidget(panel)
        return area

    def page_index(self, key: str) -> int:
        """某一页在标签栏里的下标（页面外还套着滚动区，不能拿页面本身去反查）。"""
        area = self._page_areas.get(key)
        return -1 if area is None else self.tabs.indexOf(area)

    # —— 「设置」页的显隐 ——
    def _settings_index(self) -> int:
        return self.page_index("settings")

    def settings_visible(self) -> bool:
        """「设置」页现在是不是挂在标签栏上。"""
        index = self._settings_index()
        return index >= 0 and self.tabs.isTabVisible(index)

    def show_settings(self) -> None:
        """切到设置页（侧栏齿轮、头像、登录失败提示都走这里）。

        设置页平时不在标签栏上（用户 2026-09 要求），所以先显示再切过去；
        同时记住「进来之前在哪一页」，关掉时回那儿去。
        """
        index = self._settings_index()
        if index < 0:
            return
        current = self.tabs.currentWidget()
        if current is not None and current is not self.settings_area:
            self._settings_return = current
        self._show_workflow()
        self.tabs.setTabVisible(index, True)
        self.tabs.setCurrentIndex(index)

    def hide_settings(self) -> None:
        """把设置页收回去（保存完 / 切到别的标签页时调）。"""
        index = self._settings_index()
        if index < 0 or not self.tabs.isTabVisible(index):
            return
        if self.tabs.currentIndex() == index:
            back = getattr(self, "_settings_return", None)
            if back is None or back is self.settings_area or self.tabs.indexOf(back) < 0:
                back = self.tabs.widget(0)
            if back is not None:
                self.tabs.setCurrentWidget(back)      # 先离开这一页，再收（否则 Qt 自己换页）
        self.tabs.setTabVisible(index, False)

    def _on_tab_changed(self, index: int) -> None:
        """切到别的标签页时，把「设置」页收回去。"""
        if getattr(self, "settings_panel", None) is None:
            return
        if self.tabs.widget(index) is not self.settings_area:
            self.hide_settings()

    # —— 功能页 / 侧栏 ——
    def _show_workflow(self) -> None:
        """切回「生成歌曲条目」这个功能页（以后有别的功能时，流程仍然在它自己的页里）。"""
        page = self._feature_pages.get("entry")
        if page is not None:
            self.feature_stack.setCurrentWidget(page)
            self.sidebar.set_current_feature("entry")

    def _on_feature_selected(self, key: str) -> None:
        page = self._feature_pages.get(key)
        if page is None:
            return
        self.feature_stack.setCurrentWidget(page)
        self.sidebar.set_current_feature(key)
        self.set_status(f"已切换到：{self._feature_label(key)}")

    def _feature_label(self, key: str) -> str:
        return self.sidebar.label_for(key)

    def _on_settings_saved(self) -> None:
        """设置保存后：各页重新读一遍配置，并把「设置」页从标签栏上收回去。"""
        for panel in self._panels.values():
            handler = getattr(panel, "on_settings_changed", None)
            if callable(handler):
                handler()
        self.hide_settings()              # 保存成功（saved 信号）才走到这里
        self._apply_font_scale()          # 「字号随窗口缩放」可能刚被改掉（字体在选的时候就套好了）

    def _on_font_picked(self, font_file: str, font_family: str) -> None:
        """「设置」页刚选好字体文件（还没保存）：先把字体换上去，保存只负责写进 config.yaml。"""
        self._apply_font_scale(font_file, font_family)

    # —— 字号跟着窗口大小走 ——

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_timer.start()

    def _apply_font_scale(self, font_file: Optional[str] = None,
                          font_family: Optional[str] = None) -> None:
        """窗口变大/变小 → 整体字号跟着缩放（可在「设置」页关掉），并套用刚选的应用字体。

        缩放系数是量化过的（每 0.05 一档），所以拖窗口时不会一直重排；
        字号或字体真的变了才重套样式表 + 通知挂过 `theme.scale_font` 的控件。
        两个参数只在「设置页刚选好字体、还没保存」时传（见 `SettingsPanel.font_changed`）：
        **不传就一个字都不动字体**。这里是被 resize 定时器反复调的地方，要是每次都回读
        config.yaml，设置页里刚选、还没保存的字体就会被改回去（用户 2026-09 报的
        「改窗口大小原本切换成功的字体又变回默认字体」）。config.yaml 里的字体在启动时
        （`launch()`）和设置页保存后生效——选字体那一刻 `SettingsPanel` 已经把字体套好了，
        保存只是把路径写进文件，缩放这条路不需要再读一遍。
        这个函数是定时器/槽里跑的，抛异常会让进程 abort，所以整段兜住。
        """
        # 显式传参 = 设置页刚改过字体，theme 里的值可能已经被那一页改掉了，
        # 所以「有没有变」不能只看 theme，收到通知就得重套一遍样式表
        forced = font_file is not None or font_family is not None
        try:
            from config.config import get_config
            enabled = bool(get_config().font_scale_with_window)
        except Exception:                             # noqa: BLE001 - 读不到就照默认（开着）来
            enabled = True
        try:
            changed = forced
            if forced:
                # 字体文件优先：文件里的家族名盖过 font_family（用户可能选了没装进系统的字体）
                theme.apply_font(str(font_family or ""), str(font_file or ""))
            value = theme.scale_for(self.width(), self.height()) if enabled else 1.0
            if theme.set_scale(value):
                changed = True
            if not changed:
                return
            app = QtWidgets.QApplication.instance()
            if app is not None:
                theme.apply_theme(app)                # 应用级字体 + 样式表
            theme.rescale(self)                       # 对话记录 / 日志 / 等宽框等
            _settle_layout(self)                      # 字号变了 → 当场把几何算好（别留一帧旧宽度）
        except Exception as error:                    # noqa: BLE001
            logging.error("字号缩放失败：%s", error, exc_info=error)

    def _connect(self) -> None:
        self.bridge.asked.connect(self._on_asked)
        self.bridge.output.connect(self._on_output)
        self.bridge.log.connect(self.log_tab.append)
        self.bridge.status.connect(self._on_status)
        self.bridge.done.connect(self._on_done)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.crashed.connect(self._on_callback_error)
        self._panels["style"].saved.connect(lambda result: self._finish_panel("style", result))
        self._panels["style"].cancelled.connect(lambda: self._finish_panel("style", None))
        self._panels["lyrics"].saved.connect(lambda result: self._finish_panel("lyrics", result))
        self._panels["lyrics"].cancelled.connect(lambda: self._finish_panel("lyrics", None))
        self.settings_panel.saved.connect(self._on_settings_saved)
        # 「设置」页选好字体文件就立刻换（不等保存）：保存只是把路径写进 config.yaml
        self.settings_panel.font_changed.connect(self._on_font_picked)
        # 「清除对话记录」清完 → 如果这一轮已经结束，就从头再来一轮（用户 2026-09 要求）
        self.prompt_tab.restart_requested.connect(self._restart_flow)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # —— 工作线程 → 主线程 ——
    def ask_response(self, prompt: str, auto_strip: bool = True,
                     checker: Callable[[str], bool] = None) -> str:
        request = PromptRequest(kind="response", prompt=prompt, auto_strip=auto_strip,
                                checker=checker)
        return self._ask(request)

    def ask_choices(self, prompt: str, choices: Sequence[str], allow_zero: bool = False) -> int:
        request = PromptRequest(kind="choices", prompt=prompt, choices=list(choices),
                                allow_zero=allow_zero)
        return self._ask(request)

    def ask_multiline(self, prompt: str, auto_strip: bool = True) -> List[str]:
        request = PromptRequest(kind="multiline", prompt=prompt, auto_strip=auto_strip)
        return self._ask(request)

    def _ask(self, request: PromptRequest) -> Any:
        self.bridge.asked.emit(request)
        self._raise_window()
        return request.wait()

    def _raise_window(self) -> None:
        """提问时把窗口顶到前面（流程在后台线程跑，窗口可能被压在别的窗口后面）。"""
        window = self
        QtCore.QMetaObject.invokeMethod(window, "_bring_to_front", QtCore.Qt.QueuedConnection)

    @QtCore.pyqtSlot()
    def _bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    @QtCore.pyqtSlot(object)
    def _on_asked(self, request: PromptRequest) -> None:
        if request.kind == "panel":
            self._start_panel(request)
            return
        self._show_workflow()
        self.tabs.setCurrentWidget(self.prompt_tab)
        self.prompt_tab.start_request(request)

    # —— 编辑器页 ——
    def run_style_editor(self, initial_wiki: str = "", cover_image: Any = None,
                         lyrics_hover: bool = False):
        """打开「样式」页并等用户保存；取消返回 None。"""
        request = PromptRequest(kind="panel", panel="style",
                                payload={"initial": initial_wiki, "cover": cover_image,
                                         "hover": lyrics_hover})
        return self._ask(request)

    def run_lyrics_editor(self, api):
        """打开「歌词」页并等用户点「完成」；取消返回 None。"""
        request = PromptRequest(kind="panel", panel="lyrics", payload={"api": api})
        return self._ask(request)

    def run_submit_editor(self, api) -> bool:
        """打开「提交」页。提交页是流程的最后一步，所以不等用户点完就返回。"""
        request = PromptRequest(kind="panel", panel="submit", payload={"api": api})
        self.bridge.asked.emit(request)
        self._raise_window()
        return bool(request.wait())

    def _start_panel(self, request: PromptRequest) -> None:
        key = request.panel
        panel = self._panels.get(key)
        if panel is None:
            request.done(error=f"未知的编辑器页：{key}")
            return
        index = self.page_index(key)
        self.tabs.setTabEnabled(index, True)
        self.tabs.setCurrentIndex(index)
        panel.start(request.payload)
        if key == "submit":
            # 提交页不需要「保存后回到流程」：点亮即完成
            request.done(True)
            return
        self._panel_requests[key] = request
        self.set_status({"style": "样式编辑器已打开，改完点「保存并继续」",
                         "lyrics": "歌词编辑器已打开，改完点「完成」"}.get(key, ""))

    def _finish_panel(self, key: str, result: Any) -> None:
        request = self._panel_requests.pop(key, None)
        if request is None:
            return
        index = self.page_index(key)
        self.tabs.setTabEnabled(index, False)
        if result is None:
            self.prompt_tab.append_history(
                f"<span style='color:#54595d'>（{PANEL_TITLES[key][0]}编辑器已取消，未做修改）</span>")
        request.done(result)

    # —— 日志 / 状态 ——
    def set_status(self, text: str) -> None:
        """状态文字；经信号回主线程，工作线程也能安全调。"""
        self.bridge.status.emit(str(text))

    @QtCore.pyqtSlot(str)
    def _on_status(self, text: str) -> None:
        # 长报错（出错：…整段异常…）只显示前半句，全文挂 tooltip
        widgets.set_status_text(self.status_label, text)

    def append_log(self, text: str, level: str = "") -> None:
        """往「日志」页写一行（同样经信号，任意线程都能调）。"""
        self.bridge.log.emit(str(text), level)

    @QtCore.pyqtSlot(str, bool)
    def _on_output(self, text: str, is_error: bool) -> None:
        self.log_tab.append(text, "ERROR" if is_error else "")

    # —— 流程结束 ——
    def _restart_flow(self) -> None:
        """「清除对话记录」之后：把界面恢复成刚打开的样子，再从头跑一轮（重新问「歌名？」）。

        这一轮**还在跑也照做**（用户 2026-09 要求）：先让那一轮停下来（它的下一次提问会
        抛 `RunCancelled`，不会弹错窗），再清掉它生成的东西（对话记录、日志、三个编辑器页），
        然后重新开始。
        """
        if self._start_flow is None:              # 没有流程可跑（比如单测里手搓的窗口）
            self.set_status("已清除对话记录")
            return
        aborting = self._flow_running
        if aborting:
            self._abort_running_flow()
        self._reset_for_new_run()
        self.append_log("已清除对话记录：上一轮生成已放弃，界面已恢复成刚打开的样子。"
                        if aborting else "已清除对话记录，从头开始新一轮生成。")
        self.set_status("已清除对话记录，重新开始…")
        self._start_flow()

    def _abort_running_flow(self) -> None:
        """让正在跑的那一轮停下来：标记它作废，并把悬着的提问 / 编辑器请求放掉。"""
        from utils import ui as ui_facade
        ui_facade.cancel_run()
        for key in list(self._panel_requests):
            self._finish_panel(key, None)             # 编辑器页上等着的请求也放掉
        self.prompt_tab.cancel_request()

    def _reset_for_new_run(self) -> None:
        """把界面恢复成刚打开的样子（对话记录 / 日志 / 输入区 / 三个编辑器页）。"""
        self.prompt_tab.reset()
        self.log_tab.clear()
        self._reset_panels()
        self._finished = False                        # 新一轮还会报 done / failed
        self._flow_running = False

    def _reset_panels(self) -> None:
        """清掉样式 / 歌词 / 提交三页的旧内容：重新开始时不该还看得到上一首歌的东西。"""
        for panel in self._panels.values():
            reset = getattr(panel, "reset", None)
            if callable(reset):
                reset()

    def report_done(self, output_dir: Any = None) -> None:
        """（可从工作线程调）流程正常结束。"""
        self.bridge.done.emit(output_dir)

    def report_error(self, message: str) -> None:
        """（可从工作线程调）流程失败。"""
        self.bridge.failed.emit(str(message), "")

    @QtCore.pyqtSlot(object)
    def _on_done(self, output_dir: Any) -> None:
        self._flow_running = False
        if self._finished:
            return
        self._finished = True
        self._show_workflow()
        self.tabs.setCurrentWidget(self.prompt_tab)
        self.set_status("已完成")
        self.prompt_tab.append_history(
            "<b style='color:#14866d'>生成流程已结束。</b>"
            "条目 wikitext 已写入输出目录；没有开启提交窗口时可直接用编辑器打开该文件。")
        # 输入区收回单行页：那颗红色的「清除对话记录」在那儿，不然停在选项页上就看不见它
        self.prompt_tab.finish("本轮生成已完成，点「清除对话记录」可以重新开始")
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("完成")
        box.setText("生成流程已结束。")
        if output_dir:
            box.setInformativeText(f"输出目录：{output_dir}")
            open_button = box.addButton("打开输出文件夹", QtWidgets.QMessageBox.ActionRole)
            box.addButton("关闭", QtWidgets.QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is open_button:
                from utils import ui as ui_facade
                ui_facade.open_folder(output_dir)
        else:
            box.addButton("关闭", QtWidgets.QMessageBox.AcceptRole)
            box.exec_()

    @QtCore.pyqtSlot(str, str)
    def _on_failed(self, message: str, trace: str) -> None:
        self._flow_running = False
        self._finished = True
        self._show_workflow()
        self.tabs.setCurrentWidget(self.log_tab)
        if trace:
            self.log_tab.append(trace, "ERROR")
        self.set_status("出错：" + message.splitlines()[0][:80])
        self.prompt_tab.append_history(
            f"<b style='color:#b32424'>出错了：{PromptTab._escape(message)}</b>"
            "<br/>完整堆栈见「日志」页。")
        self.prompt_tab.finish("流程出错已结束，完整堆栈见「日志」页")
        QtWidgets.QMessageBox.critical(self, "出错", message)

    @QtCore.pyqtSlot(str, str)
    def _on_callback_error(self, message: str, trace: str) -> None:
        """界面自己的回调出错：记下来、切到日志页，但**不**结束生成流程。"""
        if trace:
            self.log_tab.append(trace, "ERROR")
        self.set_status("界面出错（流程仍在跑）：" + message.splitlines()[0][:80])

    # —— 头像 / 登录 Vocawiki ——
    def _start_avatar_watch(self) -> None:
        """定时看一眼登录状态：流程里自己登录成功时，侧栏头像也要跟着变。"""
        self._avatar_workers = []
        self._avatar_timer = QtCore.QTimer(self)
        self._avatar_timer.setInterval(1500)
        self._avatar_timer.timeout.connect(self._sync_avatar)
        self._avatar_timer.start()
        self._sync_avatar()

    def _sync_avatar(self, fetch: bool = False) -> None:
        """把侧栏头像同步成当前登录状态（未登录 = 站点默认头像的占位）。"""
        logged_in = login.is_logged_in()
        username = login.current_user() if logged_in else ""
        state = (logged_in, username)
        if not fetch and state == self._avatar_state:
            return
        self._avatar_state = state
        if not logged_in:
            self._avatar_image = None
        self.sidebar.set_avatar(username, logged_in, self._avatar_image)
        if logged_in and (fetch or self._avatar_image is None):
            self._fetch_avatar_async(username)

    def _fetch_avatar_async(self, username: str) -> None:
        """后台取头像：先试普通请求（voca.wiki 的图片被 Cloudflare 挡，基本拿不到），
        同时准备好「按用户 ID 拼出来的头像文件地址」，回来后在主线程用 WebEngine 去取。"""
        worker = FunctionWorker(avatar_lib.fetch_avatar, username, avatar_lib.FETCH_SIZE)
        worker.done.connect(lambda result: self._on_avatar_fetched(username, result))
        worker.start()
        self._avatar_workers = [w for w in self._avatar_workers if w.isRunning()]
        self._avatar_workers.append(worker)               # 留住引用，别被 GC 掉

    def _on_avatar_fetched(self, username: str, result) -> None:
        """普通请求的结果：拿到字节就用，没拿到且有「头像文件地址」就换 WebEngine 取。"""
        if isinstance(result, dict):                      # FunctionWorker 出错时回 dict
            result = (None, "")
        data, url = result if isinstance(result, tuple) else (None, "")
        if data is None and url and avatar_lib.WebEngineLoader.available():
            self._load_avatar_with_webengine(username, url)
            return
        self._on_avatar_loaded(username, data)

    def _load_avatar_with_webengine(self, username: str, url: str) -> None:
        """借 WebEngine 取头像图片（它得在主线程跑，所以放在这里而不是工作线程里）。"""
        loader = avatar_lib.WebEngineLoader(self)
        loader.done.connect(lambda _url, data: self._on_avatar_from_webengine(username, data))
        loader.load(url)
        self._avatar_loader = loader                      # 留住引用，别被 GC 掉

    def _on_avatar_from_webengine(self, username: str, data) -> None:
        if isinstance(data, (bytes, bytearray)) and data:
            avatar_lib.store_bytes(username, avatar_lib.FETCH_SIZE, bytes(data))
        self._on_avatar_loaded(username, bytes(data) if data else None)

    def _on_avatar_loaded(self, username: str, data) -> None:
        if isinstance(data, dict):                        # FunctionWorker 出错时回 dict
            data = None
        if not login.is_logged_in() or login.current_user() != username:
            return                                        # 期间又退出/换号了，丢掉这次结果
        if data is None:
            # INFO 而不是 DEBUG：「日志」页默认看得到——不然用户只能看见一个占位头像，
            # 不知道是没登录还是取不到站点头像（2026-09 实测：图片本身被 Cloudflare 挡）
            self.append_log("没取到 Vocawiki 头像图片（站点图片地址被 Cloudflare 挡，"
                            "或这个用户名在站点上查不到）。先用用户名首字母头像代替。",
                            "INFO")
        self._avatar_image = data
        self.sidebar.set_avatar(username, True, data)

    def _on_avatar_clicked(self) -> None:
        if login.is_logged_in():
            self._show_account_menu()
        else:
            self.login_vocawiki()

    def login_vocawiki(self) -> None:
        """用 wiki_credentials.yaml 里的账号登录（后台线程，不卡界面）。"""
        if self._login_thread is not None and self._login_thread.isRunning():
            return
        try:
            from config.config import get_wiki_credentials
            username, password = get_wiki_credentials()
        except Exception as e:                            # noqa: BLE001
            username = password = ""
            logging.warning("读取 Vocawiki 凭据失败：%s", e)
        if not username or not password:
            self.set_status("还没配置 Vocawiki 账号，已切到「设置」页的「账号与密钥」")
            self.show_settings()
            return
        self.sidebar.set_avatar_busy(True)
        self.set_status(f"正在登录 Vocawiki：{username} …")
        worker = FunctionWorker(login.login, username, password)
        worker.done.connect(self._on_login_done)
        worker.start()
        self._login_thread = worker

    def _on_login_done(self, result) -> None:
        self.sidebar.set_avatar_busy(False)
        ok = result is True or (isinstance(result, dict) and bool(result.get("ok")))
        if ok:
            user = login.current_user() or "Vocawiki"
            self.set_status(f"已登录 Vocawiki：{user}")
            self.append_log(f"已登录 Vocawiki：{user}")
            self._sync_avatar(fetch=True)
            return
        self.set_status("登录 Vocawiki 失败，检查账号 / 机器人密码或网络")
        self.append_log("登录 Vocawiki 失败（详细原因见上文日志）。", "ERROR")
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("登录失败")
        box.setText("登录 Vocawiki 失败。")
        box.setInformativeText(
            "请检查「设置」页里「账号与密钥」的用户名与密码"
            "（建议使用机器人密码，用户名为 账户名@机器人名），或检查网络 / 代理。")
        settings_button = box.addButton("去设置页", QtWidgets.QMessageBox.ActionRole)
        box.addButton("关闭", QtWidgets.QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is settings_button:
            self.show_settings()

    def _show_account_menu(self) -> None:
        """已登录时点头像：账号信息 + 重新登录 / 退出登录 / 账号设置。"""
        user = login.current_user() or "Vocawiki"
        menu = QtWidgets.QMenu(self)
        header = menu.addAction(f"已登录：{user}")
        header.setEnabled(False)
        menu.addSeparator()
        relogin = menu.addAction(icons.icon("refresh", 16, theme.TEXT_QUIET), "重新登录")
        logout = menu.addAction(icons.icon("logout", 16, theme.TEXT_QUIET), "退出登录")
        menu.addSeparator()
        settings = menu.addAction(icons.icon("tune", 16, theme.TEXT_QUIET), "账号与密钥设置…")
        button = self.avatar_button
        point = button.mapToGlobal(QtCore.QPoint(button.width() + 6, button.height()))
        chosen = menu.exec_(QtCore.QPoint(point.x(), point.y() - menu.sizeHint().height()))
        if chosen is relogin:
            self.login_vocawiki()
        elif chosen is logout:
            self.logout_vocawiki()
        elif chosen is settings:
            self.show_settings()

    def logout_vocawiki(self) -> None:
        """退出登录（后台请求服务端，本地会话一定会清掉）。"""
        if self._logout_thread is not None and self._logout_thread.isRunning():
            return
        self.set_status("正在退出 Vocawiki 登录…")
        worker = FunctionWorker(login.logout)
        worker.done.connect(self._on_logout_done)
        worker.start()
        self._logout_thread = worker

    def _on_logout_done(self, _result) -> None:
        avatar_lib.clear_cache()
        self._avatar_state = (False, "")
        self._avatar_image = None
        self.sidebar.set_avatar("", False, None)
        self.set_status("已退出 Vocawiki 登录")
        self.append_log("已退出 Vocawiki 登录。")

    # —— 关窗 ——
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        timer = getattr(self, "_avatar_timer", None)
        if timer is not None:
            timer.stop()
        if not self._finished and self._panels:
            answer = QtWidgets.QMessageBox.question(
                self, "确认退出", "生成流程还没结束，确定要退出吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


# ---------------------------------------------------------------- 启动

def install_exception_guard(bridge: _Bridge) -> Callable:
    """给界面兜底：槽函数里未处理的异常在 PyQt5 下会让整个进程 abort，
    换成记日志 + 状态栏提示，流程还能继续跑。返回原来的 hook，便于恢复。
    """
    previous = sys.excepthook

    def hook(kind, value, tb):
        trace = "".join(traceback.format_exception(kind, value, tb))
        try:
            bridge.crashed.emit(str(value) or kind.__name__, trace)
        except Exception:                                  # 兜底里再出错就只打日志
            logging.error("界面回调出错，且上报失败：\n%s", trace)

    sys.excepthook = hook
    return previous


def _set_app_user_model_id() -> None:
    """Windows 任务栏图标：不设 AppUserModelID 时，源码运行会显示 python.exe 的图标
    （打包后的 exe 不需要，它自带）。设不上也不影响运行。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TimeRen.VocawikiCreateTool")
    except Exception as e:                           # noqa: BLE001 - 设不上就算了
        logging.debug("设置 AppUserModelID 失败：%s", e)


def _settle_layout(widget: QtWidgets.QWidget) -> None:
    """把还在队列里的布局请求立刻处理掉（等价于让事件循环这一轮马上把几何算完）。

    Qt 的布局是**延迟**的：字号变大时控件的 `sizeHint()` 当场就变了，但真正的几何要等下一轮
    事件循环处理 LayoutRequest 才算——这中间控件还是旧字号量出来的宽度。按钮里的字属于
    「字变长了、宽度没跟上」的那个方向，于是最后一个字会被裁掉（用户 2026-09 报的
    「清除对话记录」少一个「录」、「提交到 Vocawiki」少一个「i」都是这么来的：
    字已经是 13pt，按钮还留着 10.5pt 量出来的宽）。
    ⚠️ 只 `updateGeometry()` + 把队列跑空**不够**：布局自己缓存着各控件的 sizeHint，
    字号是套样式表时悄悄换的（没有走 `setFont`），缓存里那份还是旧字号的；
    要 `layout.invalidate()` 把缓存丢掉，再**亲自 `activate()`** 一次，几何才会用新宽度重排
    （实测：只跑队列时按钮一直卡在 165px、提示已经是 180px；补上 invalidate+activate 之后
    1600/1180/1340 三种窗口宽度下按钮宽都等于提示宽）。
    深→浅（子控件在前、外层的布局在后）走一遍，外层拿到的才是子控件的新尺寸。
    这条路上抛异常会让进程 abort（定时器/槽里跑的），所以整段兜住。
    """
    try:
        tree = [widget] + list(widget.findChildren(QtWidgets.QWidget))
        for child in tree:
            layout = child.layout()
            if layout is not None:
                layout.invalidate()
            child.updateGeometry()
        QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.LayoutRequest)
        for child in reversed(tree):
            layout = child.layout()
            if layout is not None:
                layout.activate()
    except Exception as error:                        # noqa: BLE001
        logging.debug("收尾重排布局失败：%s", error)


def launch(flow: Callable[[], Any], title: Optional[str] = None,
           on_done: Optional[Callable[[Any], None]] = None) -> int:
    """建主窗口 + 后台线程跑 flow，进入 Qt 事件循环；返回进程退出码。"""
    from utils import ui as ui_facade
    # 任务栏图标：要在建窗口之前设好
    _set_app_user_model_id()
    # 用 QtWebEngine 做提交预览时必须在建 QApplication 之前设好这个属性
    try:
        import PyQt5.QtWebEngineWidgets  # noqa: F401
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)
    except Exception:                                # 没装 / 起不来都行，预览会退回浏览器
        pass
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(True)

    # 先读一遍配置：设置页、AI 面板、界面字体第一眼就该是真配置，而不是 dataclass 默认值
    try:
        from config.config import config_path, get_config, load_config
        load_config(config_path())
        theme.apply_font(str(getattr(get_config(), "font_family", "") or ""),
                         str(getattr(get_config(), "font_file", "") or ""))
    except Exception as e:                           # noqa: BLE001 - 读不了就照默认值走
        logging.warning("载入配置失败，界面先按默认值显示：%s", e)

    theme.apply_theme(app)
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    window = MainWindow(title=title)
    if icon is not None:
        window.setWindowIcon(icon)
    window.show()
    ui_facade._window = window
    ui_facade._app = app
    previous_streams = (sys.stdout, sys.stderr)
    install_stream_capture(window.bridge)
    previous_hook = install_exception_guard(window.bridge)
    handler = QtLogHandler(window.bridge)
    handler.setFormatter(logging.Formatter("%(name)s :: %(levelname)-8s :: %(message)s"))
    logging.getLogger().addHandler(handler)

    def work() -> None:
        ui_facade.begin_run()                        # 给这一轮挂上「被放弃」的开关
        try:
            result = flow()
        except ui_facade.RunCancelled:
            # 用户按了「清除对话记录」：界面那边已经复位并另开了一轮，这里安静收工
            logging.info("这一轮生成已放弃（清除对话记录）。")
            return
        except Exception as e:                       # noqa: BLE001 - 任何异常都要报给界面
            logging.error(traceback.format_exc())
            window.bridge.failed.emit(str(e) or e.__class__.__name__, traceback.format_exc())
            return
        if on_done is not None:
            try:
                on_done(result)
            except Exception as e:                   # noqa: BLE001
                logging.error("收尾处理失败：%s", e, exc_info=e)
        window.bridge.done.emit(result)

    def start_flow() -> None:
        """跑一遍生成流程（后台线程）。「清除对话记录 → 重新开始」会再调一次。"""
        window._flow_running = True
        threading.Thread(target=work, name="vocawiki-flow", daemon=True).start()

    # 挂在窗口上：PromptTab 的「清除对话记录」清完后由 MainWindow 调它重来一轮
    window._start_flow = start_flow
    start_flow()
    try:
        return app.exec_()
    finally:
        logging.getLogger().removeHandler(handler)
        sys.excepthook = previous_hook
        sys.stdout, sys.stderr = previous_streams
        ui_facade._window = None
        ui_facade._app = None


def _app_icon() -> Optional[QtGui.QIcon]:
    """窗口（标题栏 / 任务栏）图标。

    按顺序找：
    1. 程序目录下的 assets/icon.{ico,png}（源码运行、或 exe 旁边的资源目录）；
    2. 打进去的包内资源 `sys._MEIPASS/assets/icon.*`（build.py 的 --add-data）；
    3. 直接读可执行文件自己的图标（Windows 上 build.py 用 --icon 嵌好了，最保险）。
    """
    candidates: List[Path] = []
    try:
        from config.config import application_path
        candidates += [Path(application_path) / "assets" / name for name in _ICON_NAMES]
    except Exception:                                # 单测里可能还没配好 config
        pass
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates += [Path(bundle) / "assets" / name for name in _ICON_NAMES]
    for path in candidates:
        if path.is_file():
            icon = QtGui.QIcon(str(path))
            if not icon.isNull():
                return icon
    if getattr(sys, "frozen", False):
        icon = QtGui.QIcon(sys.executable)           # exe 里嵌的那张
        if not icon.isNull():
            return icon
    return None
