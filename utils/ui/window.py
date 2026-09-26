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
from utils.ui import icons, theme
from utils.ui.sidebar import SideBar
from utils.ui.workers import FunctionWorker

APP_TITLE = "Vocawiki 条目辅助工具"

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._request: Optional[PromptRequest] = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.history = QtWidgets.QTextBrowser(self)
        self.history.setOpenExternalLinks(True)
        self.history.setStyleSheet(
            f"QTextBrowser {{ background: {theme.BG}; border: 1px solid {theme.BORDER}; }}")
        layout.addWidget(self.history, 1)

        self.question = QtWidgets.QLabel("准备中…", self)
        self.question.setWordWrap(True)
        font = self.question.font()
        font.setBold(True)
        self.question.setFont(font)
        layout.addWidget(self.question)

        self.stack = QtWidgets.QStackedWidget(self)
        layout.addWidget(self.stack)
        self.stack.addWidget(self._build_text_page())
        self.stack.addWidget(self._build_multiline_page())
        self.stack.addWidget(self._build_choices_page())

        self.hint = QtWidgets.QLabel("", self)
        self.hint.setStyleSheet(theme.color_style(theme.DANGER))
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.set_busy(False)

    # —— 三种输入区的搭建 ——
    def _build_text_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        self.text_input = QtWidgets.QLineEdit(page)
        self.text_input.setPlaceholderText("在这里输入后按回车")
        self.text_input.returnPressed.connect(self.submit_text)
        row.addWidget(self.text_input, 1)
        self.text_button = QtWidgets.QPushButton("确定", page)
        self.text_button.setDefault(True)
        self.text_button.clicked.connect(self.submit_text)
        row.addWidget(self.text_button)
        return page

    def _build_multiline_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        self.multiline_input = QtWidgets.QPlainTextEdit(page)
        self.multiline_input.setPlaceholderText("整段粘贴到这里，然后点「完成」")
        self.multiline_input.setMinimumHeight(150)
        column.addWidget(self.multiline_input, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.multiline_button = QtWidgets.QPushButton("完成", page)
        self.multiline_button.clicked.connect(self.submit_multiline)
        row.addWidget(self.multiline_button)
        column.addLayout(row)
        return page

    def _build_choices_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget(self)
        self.choices_layout = QtWidgets.QGridLayout(page)
        self.choices_layout.setContentsMargins(0, 0, 0, 0)
        self.choices_button = QtWidgets.QPushButton("取消本次选择", page)
        self.choices_button.clicked.connect(lambda: self._choose(0))
        return page

    # —— 界面状态 ——
    def set_busy(self, busy: bool) -> None:
        self.stack.setEnabled(busy)
        self.question.setEnabled(busy)

    def append_history(self, text: str) -> None:
        self.history.append(text)
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # —— 主线程收到请求 ——
    def start_request(self, request: PromptRequest) -> None:
        self._request = request
        self.hint.setText("")
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

    def _fill_choices(self, request: PromptRequest) -> None:
        while self.choices_layout.count():
            item = self.choices_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.choices_button:
                widget.deleteLater()
        choices = list(request.choices)
        if request.allow_zero:
            choices = ["都不要（留空）", *choices]
        for index, choice in enumerate(choices):
            button = QtWidgets.QPushButton(f"{index}. {choice}", self)
            button.clicked.connect(lambda _checked, value=index: self._choose(value))
            self.choices_layout.addWidget(button, index // 2, index % 2)
        # 选项按钮够用就不显示「取消」，避免和新按钮混在一起
        self.choices_button.setVisible(False)

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
            self.hint.setText(f"「{value}」不符合要求，请重新输入。")
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
            self.hint.setText("还没有内容，粘贴后再点「完成」。")
            return
        self._finish(lines)

    def _choose(self, value: int) -> None:
        request = self._request
        if request is None:
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
        self.clear_button.clicked.connect(lambda: self.view.clear())
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.view = QtWidgets.QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.view.setFont(theme.mono_font(10))
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ font-family: {theme.MONO_STACK}; font-size: 12px; "
            f"background: {theme.BG}; border: 1px solid {theme.BORDER}; }}")
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

    def copy_all(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.view.toPlainText())


# ---------------------------------------------------------------- 主窗口

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, title: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.bridge = _Bridge()
        self.setWindowTitle(title or APP_TITLE)
        self.resize(1260, 880)
        self._finished = False
        self._panels = {}
        self._panel_requests = {}
        self._avatar_state = (False, "")        # (已登录, 用户名)：用来判断要不要刷新头像
        self._avatar_image = None               # 下载到的真实头像字节（可能为 None）
        self._login_thread = None
        self._logout_thread = None
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
        """三个编辑器页先建好但不可点，流程走到哪一步再点亮哪一页；设置页始终可点。"""
        from utils.ui.lyrics_panel import LyricsPanel
        from utils.ui.settings_panel import SettingsPanel
        from utils.ui.style_panel import StylePanel
        from utils.ui.submit_panel import SubmitPanel
        panels = {"style": StylePanel(self), "lyrics": LyricsPanel(self),
                  "submit": SubmitPanel(self)}
        for key, (label, tooltip) in PANEL_TITLES.items():
            panel = panels[key]
            panel.setToolTip(tooltip)
            index = self.tabs.addTab(panel, label)
            self.tabs.setTabEnabled(index, False)
            self._panels[key] = panel
        self.panels = panels
        self.settings_panel = SettingsPanel(self)
        index = self.tabs.addTab(self.settings_panel, "设置")
        self.tabs.setTabToolTip(index, "可视化修改配置与账号 / 密钥（侧栏底部的齿轮也是这里）")

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

    def show_settings(self) -> None:
        """切到设置页（侧栏齿轮与样式页的「去设置」都走这里）。"""
        self._show_workflow()
        self.tabs.setCurrentWidget(self.settings_panel)

    def _on_settings_saved(self) -> None:
        """设置保存后，让各页重新读一遍配置（例如样式页的 AI 开关与密钥）。"""
        for panel in self._panels.values():
            handler = getattr(panel, "on_settings_changed", None)
            if callable(handler):
                handler()

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
        self._panels["style"].settings_requested.connect(self.show_settings)
        self._panels["lyrics"].saved.connect(lambda result: self._finish_panel("lyrics", result))
        self._panels["lyrics"].cancelled.connect(lambda: self._finish_panel("lyrics", None))
        self.settings_panel.saved.connect(self._on_settings_saved)

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
        index = self.tabs.indexOf(panel)
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
        index = self.tabs.indexOf(self._panels[key])
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
        self.status_label.setText(str(text))

    def append_log(self, text: str, level: str = "") -> None:
        """往「日志」页写一行（同样经信号，任意线程都能调）。"""
        self.bridge.log.emit(str(text), level)

    @QtCore.pyqtSlot(str, bool)
    def _on_output(self, text: str, is_error: bool) -> None:
        self.log_tab.append(text, "ERROR" if is_error else "")

    # —— 流程结束 ——
    def report_done(self, output_dir: Any = None) -> None:
        """（可从工作线程调）流程正常结束。"""
        self.bridge.done.emit(output_dir)

    def report_error(self, message: str) -> None:
        """（可从工作线程调）流程失败。"""
        self.bridge.failed.emit(str(message), "")

    @QtCore.pyqtSlot(object)
    def _on_done(self, output_dir: Any) -> None:
        if self._finished:
            return
        self._finished = True
        self._show_workflow()
        self.tabs.setCurrentWidget(self.prompt_tab)
        self.set_status("已完成")
        self.prompt_tab.append_history(
            "<b style='color:#14866d'>生成流程已结束。</b>"
            "条目 wikitext 已写入输出目录；没有开启提交窗口时可直接用编辑器打开该文件。")
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
        self._finished = True
        self._show_workflow()
        self.tabs.setCurrentWidget(self.log_tab)
        if trace:
            self.log_tab.append(trace, "ERROR")
        self.set_status("出错：" + message.splitlines()[0][:80])
        self.prompt_tab.append_history(
            f"<b style='color:#b32424'>出错了：{PromptTab._escape(message)}</b>"
            "<br/>完整堆栈见「日志」页。")
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
        """后台下载真实头像。voca.wiki 对非浏览器客户端一律 Cloudflare 403，
        拿不到就继续用占位头像（不报错、不卡界面）。"""
        worker = FunctionWorker(avatar_lib.load_bytes, username, 128)
        worker.done.connect(lambda data: self._on_avatar_loaded(username, data))
        worker.start()
        self._avatar_workers = [w for w in self._avatar_workers if w.isRunning()]
        self._avatar_workers.append(worker)               # 留住引用，别被 GC 掉

    def _on_avatar_loaded(self, username: str, data) -> None:
        if isinstance(data, dict):                        # FunctionWorker 出错时回 dict
            data = None
        if not login.is_logged_in() or login.current_user() != username:
            return                                        # 期间又退出/换号了，丢掉这次结果
        if data is None:
            self.append_log("没取到 Vocawiki 头像图片（站点对非浏览器请求一律 403），"
                            "先用用户名首字母头像代替。", "DEBUG")
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


def launch(flow: Callable[[], Any], title: Optional[str] = None,
           on_done: Optional[Callable[[Any], None]] = None) -> int:
    """建主窗口 + 后台线程跑 flow，进入 Qt 事件循环；返回进程退出码。"""
    from utils import ui as ui_facade
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
    theme.apply_theme(app)
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    # 先读一遍配置：设置页与 AI 面板第一眼就该是真配置，而不是 dataclass 默认值
    try:
        from config.config import config_path, load_config
        load_config(config_path())
    except Exception as e:                           # noqa: BLE001 - 读不了就照默认值走
        logging.warning("载入配置失败，界面先按默认值显示：%s", e)

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
        try:
            result = flow()
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

    threading.Thread(target=work, name="vocawiki-flow", daemon=True).start()
    try:
        return app.exec_()
    finally:
        logging.getLogger().removeHandler(handler)
        sys.excepthook = previous_hook
        sys.stdout, sys.stderr = previous_streams
        ui_facade._window = None
        ui_facade._app = None


def _app_icon() -> Optional[QtGui.QIcon]:
    """程序目录下 assets/icon.{ico,png} 存在就用它当窗口图标（打包后同一位置）。"""
    try:
        from config.config import application_path
    except Exception:                                # 单测里可能还没配好 config
        return None
    for name in ("icon.ico", "icon.png"):
        path = Path(application_path) / "assets" / name
        if path.exists():
            return QtGui.QIcon(str(path))
    return None
