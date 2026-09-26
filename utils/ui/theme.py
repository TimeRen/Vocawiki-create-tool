"""界面主题：配色与全局样式表（参考 MediaWiki 的 Timeless 皮肤）。

Timeless 的视觉语言：白底内容块 + 极浅灰页面底 + 1px 细边框（#c8ccd1 / #a2a9b1）、
几乎不用圆角（2px）、克制的蓝色强调色（#36c）、次要文字用灰（#54595d）、
选中项用淡蓝底（#eaf3ff）。这里把这套 token 集中起来，界面各处只引用它们，
免得又到处散落 #6c7386 这种一次性颜色。
"""
from string import Template

from PyQt5 import QtGui, QtWidgets

# —— 配色 token（数值取自 Timeless / MediaWiki 的默认调色板）——
BG = "#ffffff"            # 内容底色
BG_PAGE = "#f8f9fa"      # 页面底 / 侧栏 / 次要按钮底
BG_SUBTLE = "#eaecf0"     # hover / 次级强调
BORDER = "#c8ccd1"        # 常规边框
BORDER_STRONG = "#a2a9b1"  # 输入框、按钮边框
TEXT = "#202122"          # 正文
TEXT_QUIET = "#54595d"    # 次要说明
TEXT_MUTED = "#72777d"    # 更弱的提示 / 占位
ACCENT = "#3366cc"        # 主色（链接蓝）
ACCENT_DARK = "#2a4b8d"   # 主色按下 / 已访问
ACCENT_SOFT = "#eaf3ff"   # 选中底
SUCCESS = "#14866d"
WARNING = "#ac6600"
DANGER = "#b32424"

FONT_STACK = '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif'
MONO_STACK = '"Cascadia Mono", Consolas, "Courier New", monospace'

# 用 $name 模板（CSS 里不会出现 $，避免 {} 与 % 的转义麻烦）
_QSS = Template("""
QWidget { font-family: $font; font-size: 12px; }
QMainWindow, QDialog { background: $bg_page; }

/* —— 标签页：Timeless 的 wiki-tabs —— */
QTabWidget::pane { background: $bg; border: 1px solid $border; border-radius: 2px; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab {
    background: $bg_subtle; color: $text; border: 1px solid $border; border-bottom: none;
    border-top-left-radius: 2px; border-top-right-radius: 2px;
    padding: 5px 14px; margin-right: 2px; margin-top: 2px;
}
QTabBar::tab:hover { background: $accent_soft; }
QTabBar::tab:selected {
    background: $bg; border-top: 2px solid $accent; font-weight: 600; margin-top: 0; padding-top: 7px;
}
QTabBar::tab:disabled { color: $text_muted; background: transparent; border-color: transparent; }

/* —— 按钮 —— */
QPushButton {
    background: $bg_page; color: $text; border: 1px solid $border_strong;
    border-radius: 2px; padding: 4px 12px;
}
QPushButton:hover { background: $bg_subtle; }
QPushButton:pressed { background: #d5d9dd; }
QPushButton:disabled { color: $text_muted; border-color: $border; background: $bg_page; }
QPushButton:default, QPushButton[accent="true"] {
    background: $accent; color: #ffffff; border-color: $accent; font-weight: 600;
}
QPushButton:default:hover, QPushButton[accent="true"]:hover { background: $accent_dark; }
QPushButton[flat="true"] {
    background: transparent; border: none; color: $accent; padding: 2px 4px; text-align: left;
}
QPushButton[flat="true"]:hover { color: $accent_dark; }

QToolButton {
    background: transparent; color: $text; border: 1px solid transparent;
    border-radius: 2px; padding: 3px;
}
QToolButton:hover { background: $bg_subtle; border-color: $border; }
QToolButton:checked { background: $accent_soft; border-color: $accent; }
QToolButton:disabled { color: $text_muted; }

/* —— 输入类 —— */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $bg; color: $text; border: 1px solid $border_strong; border-radius: 2px;
    selection-background-color: $accent_soft; selection-color: $text;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { padding: 3px 6px; }
QPlainTextEdit, QTextEdit, QTextBrowser { padding: 2px 4px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: $text_muted; background: $bg_page; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $bg; border: 1px solid $border_strong;
    selection-background-color: $accent_soft; selection-color: $text;
}

/* —— 容器 —— */
QGroupBox {
    background: $bg; border: 1px solid $border; border-radius: 2px;
    margin-top: 10px; padding: 8px 8px 6px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px; color: $text_quiet; font-weight: 600;
}
QCheckBox, QRadioButton { color: $text; spacing: 6px; }
QCheckBox:disabled, QRadioButton:disabled { color: $text_muted; }
QCheckBox::indicator, QRadioButton::indicator { width: 13px; height: 13px; }
QCheckBox::indicator {
    border: 1px solid $border_strong; border-radius: 2px; background: $bg;
}
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox::indicator:disabled { background: $bg_page; border-color: $border; }
QRadioButton::indicator { border: 1px solid $border_strong; border-radius: 7px; background: $bg; }
QRadioButton::indicator:checked { background: $accent; border-color: $accent; }

QSplitter::handle { background: $border; }
QSplitter::handle:hover { background: $border_strong; }

/* —— 列表 / 表格 —— */
QListWidget, QTreeWidget, QTableWidget {
    background: $bg; border: 1px solid $border; border-radius: 2px;
    alternate-background-color: $bg_page;
    selection-background-color: $accent_soft; selection-color: $text;
}
QHeaderView::section {
    background: $bg_page; color: $text_quiet; border: none;
    border-right: 1px solid $border; border-bottom: 1px solid $border; padding: 4px 6px;
}
QProgressBar {
    background: $bg_page; border: 1px solid $border; border-radius: 2px; text-align: center;
    color: $text;
}
QProgressBar::chunk { background: $accent; }

/* —— 滚动条：细一点，Timeless 不抢眼 —— */
QScrollBar:vertical { background: $bg_page; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $text_muted; }
QScrollBar:horizontal { background: $bg_page; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* —— 菜单 / 提示 —— */
QMenu { background: $bg; border: 1px solid $border_strong; padding: 4px; }
QMenu::item { padding: 4px 20px 4px 14px; color: $text; }
QMenu::item:selected { background: $accent_soft; color: $text; }
QMenu::item:disabled { color: $text_muted; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }
QToolTip {
    background: $bg; color: $text; border: 1px solid $border_strong; padding: 3px 6px;
}
""")


def stylesheet() -> str:
    """全局样式表（各控件的默认外观）。"""
    return _QSS.substitute(
        font=FONT_STACK, mono=MONO_STACK,
        bg=BG, bg_page=BG_PAGE, bg_subtle=BG_SUBTLE, border=BORDER,
        border_strong=BORDER_STRONG, text=TEXT, text_quiet=TEXT_QUIET,
        text_muted=TEXT_MUTED, accent=ACCENT, accent_dark=ACCENT_DARK,
        accent_soft=ACCENT_SOFT, success=SUCCESS, warning=WARNING, danger=DANGER,
    )


def palette() -> QtGui.QPalette:
    """QPalette：QSS 管不到的控件（QMessageBox 等）也跟着走这套配色。"""
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(BG_PAGE))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(BG))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(BG_PAGE))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor(BG_PAGE))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(ACCENT))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.Link, QtGui.QColor(ACCENT))
    pal.setColor(QtGui.QPalette.LinkVisited, QtGui.QColor(ACCENT_DARK))
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(BG))
    pal.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Mid, QtGui.QColor(BORDER))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(TEXT_MUTED))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(TEXT_MUTED))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, QtGui.QColor(TEXT_MUTED))
    return pal


def apply_theme(app: QtWidgets.QApplication) -> None:
    """给整个应用套上主题：Fusion 风格 + 调色板 + 样式表。"""
    app.setStyle(QtWidgets.QStyleFactory.create("Fusion") or app.style())
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    font = QtGui.QFont("Segoe UI")
    font.setPointSize(9)
    app.setFont(font)


def quiet_label_style() -> str:
    """次要说明文字（等价于旧代码里到处写的 QLabel { color: #6c7386; }）。"""
    return f"QLabel {{ color: {TEXT_QUIET}; }}"


def muted_label_style() -> str:
    return f"QLabel {{ color: {TEXT_MUTED}; }}"


def color_style(color: str) -> str:
    return f"QLabel {{ color: {color}; }}"


def mark_accent(*buttons: QtWidgets.QPushButton) -> None:
    """把按钮标成 Timeless 的「主按钮」（QSS 里 [accent="true"] 规则）。"""
    for button in buttons:
        button.setProperty("accent", "true")
        button.style().unpolish(button)
        button.style().polish(button)


def mark_flat(*buttons: QtWidgets.QPushButton) -> None:
    """把按钮标成「文字按钮」（无边框、蓝字、左对齐）。"""
    for button in buttons:
        button.setProperty("flat", "true")
        button.style().unpolish(button)
        button.style().polish(button)


def mono_font(size: int = 12) -> QtGui.QFont:
    font = QtGui.QFont("Consolas")
    font.setStyleHint(QtGui.QFont.Monospace)
    font.setPointSize(size)
    return font


def elide(text: str, limit: int = 60) -> str:
    """状态栏里用的短文本。"""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "BG", "BG_PAGE", "BG_SUBTLE", "BORDER", "BORDER_STRONG", "TEXT", "TEXT_QUIET",
    "TEXT_MUTED", "ACCENT", "ACCENT_DARK", "ACCENT_SOFT", "SUCCESS", "WARNING",
    "DANGER", "FONT_STACK", "MONO_STACK", "stylesheet", "palette", "apply_theme",
    "quiet_label_style", "muted_label_style", "color_style", "mark_accent",
    "mark_flat", "mono_font", "elide",
]
