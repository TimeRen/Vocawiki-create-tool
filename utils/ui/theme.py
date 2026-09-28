"""界面主题：配色与全局样式表（参考 MediaWiki 的 Timeless 皮肤）。

Timeless 的视觉语言：白底内容块 + 极浅灰页面底 + 1px 细边框（#c8ccd1 / #a2a9b1）、
几乎不用圆角（2px）、克制的蓝色强调色（#36c）、次要文字用灰（#54595d）、
选中项用淡蓝底（#eaf3ff）。这里把这套 token 集中起来，界面各处只引用它们，
免得又到处散落 #6c7386 这种一次性颜色。

应用字体可以由用户选（「设置」页里点字体栏选一个字体**文件**，或直接在 config.yaml 里写
`font_family` / `font_file`）：`apply_font()` 改完调一次 `apply_theme()` 就全局生效。
"""
import logging
import tempfile
from pathlib import Path
from string import Template
from typing import List

from PyQt5 import QtCore, QtGui, QtWidgets

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
DEFAULT_FONT_FAMILY = "Segoe UI"        # 用户没指定字体时用的那个（config.yaml 的 font_family 为空）

# 当前选中的界面字体（空串 = 默认）与它的来源文件（空串 = 按名字找系统字体）。
# 改它们请走 apply_font() / set_font_family()。
_font_family = ""
_font_file = ""

# 已经注册过的字体文件：{路径: 家族名}。Qt 里字体是全局注册的，同一个文件注册两次
# 会白白多占一份（「设置」页每次 load() 都会走一遍），所以记下来。
_loaded_font_files: dict = {}

# 字号：正文 12px 是 Qt 在 Windows 上的默认值，中文界面看着偏小，所以统一调大一档
FONT_SIZE_PX = 14          # 正文 / 按钮 / 输入框
FONT_SIZE_SMALL_PX = 12    # 次要说明（侧栏小节标题、状态条）
MONO_SIZE_PX = 13          # 等宽区域（日志、wikitext、CSS）
HISTORY_FONT_PX = 20       # 「填写信息」页的对话记录：面积大、要长时间盯着看，再大一号
QUESTION_FONT_PX = 18      # 「填写信息」页的问题那句话（“正在问你什么”要一眼就看到）
APP_FONT_PT = 10.5         # 应用级字体（≈ 14px）
CONTENT_PAD_PX = 12        # 文本框内文字距左框的留白（对话记录 / 输入框 / 问题标签都用它对齐）
DANGER_DARK = "#8f1a1a"    # 危险按钮的悬停色

# —— 字号缩放（跟着窗口大小变）——
# 基准窗口 = 设计时的 1100×768：窗口更大字号更大、更小更小，但有上下限
# （上限防止在高分屏上把界面撑爆，下限防止中文小到看不清）。
BASE_WINDOW_W = 1100
BASE_WINDOW_H = 768
SCALE_MIN = 0.85
SCALE_MAX = 1.45
SCALE_STEP = 0.05          # 量化步长：拖窗口时不必每像素都重排一次
MIN_FONT_PX = 11
FONT_BASE_ATTR = "vuBaseFont"       # 控件属性：基准字号
FONT_BOLD_ATTR = "vuFontBold"
FONT_MONO_ATTR = "vuFontMono"

_scale = 1.0

# 用 $name 模板（CSS 里不会出现 $，避免 {} 与 % 的转义麻烦）
# 注意：**字号不写在这里**（应用级的 font-size 会盖掉控件的 setFont，
# 那样字号就没法跟着窗口缩放了）——字号统一由 apply_theme 的 app.setFont
# 和 theme.scale_font() 管，这里只留字体家族。
_QSS = Template("""
QWidget { font-family: $font; }
QMainWindow, QDialog { background: $bg_page; }

/* —— 标签页：Timeless 的 wiki-tabs —— */
/* 内容区不画外框：页面里的框自己就是框，这样框线与标签左边界能对在同一竖线上 */
QTabWidget::pane { background: $bg; border: none; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab {
    background: $bg_subtle; color: $text; border: 1px solid $border; border-bottom: none;
    border-top-left-radius: 2px; border-top-right-radius: 2px;
    padding: 6px 16px; margin-right: 2px; margin-top: 2px;
}
QTabBar::tab:hover { background: $accent_soft; }
QTabBar::tab:selected {
    background: $bg; border-top: 2px solid $accent; font-weight: 600; margin-top: 0; padding-top: 8px;
}
QTabBar::tab:disabled { color: $text_muted; background: transparent; border-color: transparent; }

/* —— 按钮 —— */
QPushButton {
    background: $bg_page; color: $text; border: 1px solid $border_strong;
    border-radius: 2px; padding: 5px 14px;
}
QPushButton:hover { background: $bg_subtle; }
QPushButton:pressed { background: #d5d9dd; }
QPushButton:disabled { color: $text_muted; border-color: $border; background: $bg_page; }
QPushButton:default, QPushButton[accent="true"] {
    background: $accent; color: #ffffff; border-color: $accent; font-weight: 600;
}
QPushButton:default:hover, QPushButton[accent="true"]:hover { background: $accent_dark; }
QPushButton[danger="true"] {
    background: $danger; color: #ffffff; border-color: $danger; font-weight: 600;
}
QPushButton[danger="true"]:hover { background: $danger_dark; }
QPushButton[danger="true"]:pressed { background: $danger_dark; }
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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { padding: 4px 8px; }
QPlainTextEdit, QTextEdit, QTextBrowser { padding: 4px 6px; }
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
/* 选中时是蓝底 + 白对号：QSS 把 indicator 整个重画了，Fusion 自带的勾就不会再画，
   所以得自己给一张对号图（`$check`，运行时画到临时目录的 PNG） */
QCheckBox::indicator:checked {
    background: $accent; border-color: $accent; image: url($check);
}
QCheckBox::indicator:indeterminate {
    background: $accent; border-color: $accent; image: url($check);
}
QCheckBox::indicator:disabled { background: $bg_page; border-color: $border; }
/* 禁用但还是勾着：底色换成灰的，否则白对号会看不见 */
QCheckBox::indicator:checked:disabled,
QCheckBox::indicator:indeterminate:disabled {
    background: $border_strong; border-color: $border_strong; image: url($check);
}
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
        font=font_stack(), mono=MONO_STACK, check=_check_tick_url(),
        bg=BG, bg_page=BG_PAGE, bg_subtle=BG_SUBTLE, border=BORDER,
        border_strong=BORDER_STRONG, text=TEXT, text_quiet=TEXT_QUIET,
        text_muted=TEXT_MUTED, accent=ACCENT, accent_dark=ACCENT_DARK,
        accent_soft=ACCENT_SOFT, success=SUCCESS, warning=WARNING, danger=DANGER,
        danger_dark=DANGER_DARK,
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
    """给整个应用套上主题：Fusion 风格 + 调色板 + 样式表 + 应用级字体（带缩放）。

    字号缩放或换字体会反复调它，所以已经在 Fusion 上就不再重建 style（重建会让所有控件重新 polish）。
    """
    if app.style().objectName() != "fusion":
        app.setStyle(QtWidgets.QStyleFactory.create("Fusion") or app.style())
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    font = QtGui.QFont(font_family())
    font.setPointSizeF(max(9.0, APP_FONT_PT * _scale))
    app.setFont(font)


# ---------------------------------------------------------------- Qt 自己的文字走中文

# Qt 自带的 `qt_zh_CN.qm` 只覆盖一部分：QLineEdit、QMessageBox、标准对话框有，
# **QPlainTextEdit / QTextEdit 的右键菜单**（字符串在 `QWidgetTextControl` 上下文里）
# 与 **QMessageBox 的标准按钮**（`QPlatformTheme` 的 OK / Cancel / Yes / No…）没有 ——
# PyQt5 的轮子里压根没有 `qtbase_zh_CN.qm`（只有 zh_TW），于是这两处一直是英文。
#
# 补缺的对照表一共三张，都叫 `QT_*_ZH`，**键 = Qt 源码里的原文**（含 `&` 助记符，
# 大小写一字不差），值 = 中文：
#   * `QT_EDIT_MENU_ZH`      文本框右键菜单（`QT_EDIT_MENU_CONTEXTS` 那几个上下文）
#   * `QT_STANDARD_BUTTON_ZH` QMessageBox 等标准按钮（`QPlatformTheme` 上下文）
#   * `QT_PREVIEW_MENU_ZH`   提交页 WebEngine 预览的右键菜单 —— 在 `submit_panel.py`
#     （Chromium 内核给的，PyQt5 连 `qtwebengine_zh_CN.qm` 都没有），键同样是 Qt 原文
#
# 写表时的纪律：
#   * 键一律照抄 Qt 的样子，别自己编键名 —— Qt 找不到就当没有这条；
#   * 认不出的原文：翻译器那条路必须返回 `None`（见 `_GapTranslator`），
#     而**菜单**那条路必须原样返回（菜单总得显示点什么，见 `preview_menu_text`）；
#   * 这三张表是「补 PyQt5 的缺」，Qt 哪天自带 `qtbase_zh_CN.qm` / `qtwebengine_zh_CN.qm`
#     就可以整张删掉 —— 所以别把它们搬进 `i18n/messages.po`（那是业务文案的 catalog，
#     键是 `uploader_note` 这类符号名，和 Qt 原文不是一套命名空间）。
QT_EDIT_MENU_CONTEXTS = ("QWidgetTextControl", "QTextControl", "QPlainTextEdit", "QTextEdit")
QT_EDIT_MENU_ZH = {
    "&Undo": "撤消(&U)",
    "&Redo": "恢复(&R)",
    "Cu&t": "剪切(&T)",
    "&Copy": "复制(&C)",
    "&Paste": "粘贴(&P)",
    "Delete": "删除",
    "&Delete": "删除",
    "Select All": "选择全部",
    "&Select All": "选择全部",
    "Copy &Link Location": "复制链接地址",
}
# Qt 源码 QPlatformTheme::defaultStandardButtonText() 里那串原文
QT_STANDARD_BUTTON_ZH = {
    "OK": "确定",
    "Save": "保存",
    "Save All": "全部保存",
    "Open": "打开",
    "&Yes": "是(&Y)",
    "Yes to &All": "全部选是(&A)",
    "&No": "否(&N)",
    "N&o to All": "全部选否(&O)",
    "Abort": "中止",
    "Retry": "重试",
    "Ignore": "忽略",
    "Close": "关闭",
    "Cancel": "取消",
    "Discard": "放弃",
    "Help": "帮助",
    "Apply": "应用",
    "Reset": "重置",
    "Restore Defaults": "恢复默认值",
}

# ⚠️ 翻译器必须留引用：QTranslator 是 QObject，installTranslator 不接管所有权，
# 没人引用时会被 GC 回收 —— 那样翻译就静默失效（排查时很容易以为是「表错了」）。
_translators: List[QtCore.QTranslator] = []
_translations_language: str = ""            # 已经装过哪种语言（避免重复装）


class _GapTranslator(QtCore.QTranslator):
    """补 Qt 自带 zh_CN 缺的那两张表（文本框右键菜单 / 标准按钮，见上面 `QT_*_ZH`）。

    认不出的字符串返回 `None`（null QString）—— Qt 就是靠「是不是 null」判断翻译有没有找到，
    返回空串会被当成「翻译成了空字符串」，把下一个翻译器的结果也吞掉（实测菜单文字会整个变没）。
    """

    def translate(self, context, source_text, disambiguation=None, n=-1):  # noqa: N802 - Qt 约定
        if context in QT_EDIT_MENU_CONTEXTS:
            return QT_EDIT_MENU_ZH.get(source_text)
        if context == "QPlatformTheme":
            return QT_STANDARD_BUTTON_ZH.get(source_text)
        return None


def ui_language() -> str:
    """界面语言（`config.yaml` 的 `lang`）；读不到就返回空串（按中文处理）。"""
    try:
        from config.config import get_config
        return str(getattr(get_config(), "lang", "") or "").strip().lower()
    except Exception:                          # noqa: BLE001 - 配置没起来也别影响界面
        return ""


def install_translations(app: QtWidgets.QApplication,
                         language: str = None) -> None:
    """让 Qt 自己的文字跟着走中文：右键菜单（剪切 / 复制 / 粘贴 / 全选…）、标准按钮（确定 / 取消…）。

    只在界面语言是中文（`lang` 以 `zh` 开头，或读不到）时装；`lang: en` 就保持 Qt 默认的英文。
    先装 Qt 自带的 `qt_zh_CN`，再装补缺的 `_GapTranslator`（后装的先被查到）。
    翻译文件缺失 / 加载失败也不报错 —— 最坏就是回到英文菜单。
    """
    global _translations_language
    language = ui_language() if language is None else str(language or "").strip().lower()
    if language and not language.startswith("zh"):
        return
    if _translations_language:
        return                                  # 已经装过（换字体、重建窗口都不必再来一次）
    path = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
    base = QtCore.QTranslator()
    if base.load("qt_zh_CN", path):
        app.installTranslator(base)
        _translators.append(base)
    else:
        logging.info("找不到 Qt 的中文翻译文件（%s/qt_zh_CN.qm），标准控件文字将保持英文", path)
    gap = _GapTranslator()
    app.installTranslator(gap)                  # 这张表是纯 Python 的，打包后也在
    _translators.append(gap)
    _translations_language = language or "zh"


# ---------------------------------------------------------------- 应用字体

def font_family() -> str:
    """当前界面字体（用户没选就是 DEFAULT_FONT_FAMILY）。"""
    return _font_family or DEFAULT_FONT_FAMILY


def font_file() -> str:
    """当前界面字体来自哪个字体文件（没有就是空串）。"""
    return _font_file


def load_font_file(path: str) -> str:
    """把字体文件（.ttf / .otf / .ttc…）注册进 Qt，返回它的家族名；Qt 认不出就返回空串。

    .ttc / .otc 这类字体集合里可能有多个家族，取文件里的第一个（用户选合集时
    通常就是想要那个主家族，如 msyh.ttc → Microsoft YaHei）。
    同一个文件只注册一次（见 `_loaded_font_files`）。
    """
    path = str(path or "").strip()
    if not path:
        return ""
    if path in _loaded_font_files:
        return _loaded_font_files[path]
    family = ""
    try:
        if not Path(path).exists():
            logging.warning("字体文件不存在：%s", path)
        else:
            font_id = QtGui.QFontDatabase.addApplicationFont(path)
            families = (QtGui.QFontDatabase.applicationFontFamilies(font_id)
                        if font_id >= 0 else [])
            family = families[0] if families else ""
            if not family:
                logging.warning("字体文件里读不出字体家族：%s", path)
    except Exception as e:                        # noqa: BLE001 - 坏文件别把界面搞崩
        logging.warning("加载字体文件失败（%s）：%s", path, e)
        family = ""
    if family:
        _loaded_font_files[path] = family
    return family


def apply_font(family: str = "", path: str = "") -> bool:
    """设置界面字体：给了字体文件就用文件里的字体，否则用 family 这个名字（空串 = 默认）。

    真的变了才返回 True；调用方拿到 True 后自己 `apply_theme()` 一次（换字体要重套样式表）。
    文件读不出来时记一条日志、退回按 family 找系统字体（用户可能只是把文件挪走了）。
    """
    global _font_family, _font_file
    path = str(path or "").strip()
    family = str(family or "").strip()
    if path:
        loaded = load_font_file(path)
        if loaded:
            family = loaded
    changed = False
    if path != _font_file:
        _font_file = path
        changed = True
    if family != _font_family:
        _font_family = family
        changed = True
    return changed


def set_font_family(name: str) -> bool:
    """按字体名设置界面字体（空串 = 回到默认）；真的是变了才返回 True。

    调用方拿到 True 后需要自己 `apply_theme()` 一次——换字体意味着要重套样式表。
    按**名字**换字体时要把之前选的字体文件清掉，否则文件会一种盖着这个名字
    （见 `apply_font`，文件优先）。
    """
    return apply_font(name, "")


def font_stack() -> str:
    """QSS 的 font-family：用户选的字体排最前，后面保留原有的中文字体回退。"""
    if not _font_family:
        return FONT_STACK
    return f'"{_font_family}", {FONT_STACK}'


# 对号图只画一次（QSS 不支持 data URI，只能落到临时文件里用 url() 引）
_TICK_CACHE = {}

# 系统字体列表也只要枚举一次：Windows 上这一步要问一遍 GDI，几百毫秒起步，
# 而「设置」页会跟着主窗口一起建（单测里建几百次）——不缓存会把整个套件拖慢一倍。
_font_families: List[str] = None


def font_families() -> List[str]:
    """系统里可选的字体家族（升序，带缓存）。"""
    global _font_families
    if _font_families is None:
        try:
            _font_families = sorted(QtGui.QFontDatabase().families())
        except Exception as e:                    # noqa: BLE001 - 拿不到就只留默认字体
            logging.debug("枚举系统字体失败：%s", e)
            _font_families = []
    return list(_font_families)


def _check_tick_url(color: str = "#ffffff", size: int = 13) -> str:
    """画一张「白对号」PNG 并返回 QSS 可直接用的路径。"""
    key = (color, size)
    cached = _TICK_CACHE.get(key)
    if cached and Path(cached).exists():
        return cached
    image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(max(1.6, size * 0.15))
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    path = QtGui.QPainterPath()
    path.moveTo(size * 0.24, size * 0.52)
    path.lineTo(size * 0.43, size * 0.72)
    path.lineTo(size * 0.77, size * 0.28)
    painter.drawPath(path)
    painter.end()
    folder = Path(tempfile.gettempdir()).joinpath("vocawiki-theme")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder.joinpath(f"check-{color.lstrip('#')}-{size}.png")
    image.save(str(target), "PNG")
    url = str(target).replace("\\", "/")          # QSS 里只能用正斜杠
    _TICK_CACHE[key] = url
    return url


# ---------------------------------------------------------------- 字号缩放

def scale() -> float:
    """当前字号缩放系数（1.0 = 基准窗口）。"""
    return _scale


def scale_for(width: int, height: int) -> float:
    """按窗口大小算缩放系数：取宽/高比例里小的那个，量化到 SCALE_STEP 并限幅。"""
    if width <= 0 or height <= 0:
        return _scale
    raw = min(width / BASE_WINDOW_W, height / BASE_WINDOW_H)
    raw = max(SCALE_MIN, min(SCALE_MAX, raw))
    return round(round(raw / SCALE_STEP) * SCALE_STEP, 2)


def set_scale(value: float) -> bool:
    """设置缩放系数；真的变了才返回 True（调用方据此决定要不要重套样式）。"""
    global _scale
    value = max(SCALE_MIN, min(SCALE_MAX, float(value)))
    if abs(value - _scale) < 1e-6:
        return False
    _scale = value
    return True


def font_px(base: float) -> int:
    """基准像素字号 → 当前缩放下的像素字号。"""
    return max(MIN_FONT_PX, int(round(base * _scale)))


def scale_font(widget: QtWidgets.QWidget, base_px: float, bold: bool = False,
               mono: bool = False) -> None:
    """给控件挂一个「跟着窗口缩放」的字号。

    基准值记在控件属性上（不另外持引用），缩放后调 `rescale()` 统一重算。
    正文用 `ui_font`（像素），等宽区用 `mono_font`（点值）。
    """
    widget.setProperty(FONT_BASE_ATTR, int(base_px))
    widget.setProperty(FONT_BOLD_ATTR, bool(bold))
    widget.setProperty(FONT_MONO_ATTR, bool(mono))
    apply_tracked_font(widget)


def apply_tracked_font(widget: QtWidgets.QWidget) -> None:
    """按属性里记的基准字号给这个控件重新设一次字体。"""
    base = widget.property(FONT_BASE_ATTR)
    if base is None:
        return
    if widget.property(FONT_MONO_ATTR):
        widget.setFont(mono_font(int(base)))
    else:
        widget.setFont(ui_font(font_px(int(base)), bool(widget.property(FONT_BOLD_ATTR))))


def rescale(root: QtWidgets.QWidget) -> None:
    """按当前缩放系数重新设置 root 及其后代里挂过 scale_font 的控件。"""
    apply_tracked_font(root)
    for widget in root.findChildren(QtWidgets.QWidget):
        if widget.property(FONT_BASE_ATTR) is not None:
            apply_tracked_font(widget)


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


def mark_danger(*buttons: QtWidgets.QPushButton) -> None:
    """把按钮标成「危险按钮」（红底白字，删除 / 清空这类不可撤销的操作）。"""
    for button in buttons:
        button.setProperty("danger", "true")
        button.style().unpolish(button)
        button.style().polish(button)


def mono_font(size: int = MONO_SIZE_PX) -> QtGui.QFont:
    """等宽字体。`size` 是**像素**字号（与 MONO_SIZE_PX / 控件级 QSS 里的 13px 一致），
    且按当前缩放系数换算——以前这里当成点值用（13pt ≈ 17px），比正文还大。"""
    font = QtGui.QFont("Consolas")
    font.setStyleHint(QtGui.QFont.Monospace)
    font.setPixelSize(font_px(size))
    return font


def ui_font(pixel_size: int = FONT_SIZE_PX, bold: bool = False) -> QtGui.QFont:
    """按像素指定字号的正文字体（比点值好控制）。传进来的已经是缩放后的值。"""
    font = QtGui.QFont(font_family())
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font


def elide(text: str, limit: int = 60) -> str:
    """状态栏里用的短文本。"""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "BG", "BG_PAGE", "BG_SUBTLE", "BORDER", "BORDER_STRONG", "TEXT", "TEXT_QUIET",
    "TEXT_MUTED", "ACCENT", "ACCENT_DARK", "ACCENT_SOFT", "SUCCESS", "WARNING",
    "DANGER", "DANGER_DARK", "FONT_STACK", "MONO_STACK", "DEFAULT_FONT_FAMILY",
    "FONT_SIZE_PX",
    "FONT_SIZE_SMALL_PX", "MONO_SIZE_PX", "HISTORY_FONT_PX", "QUESTION_FONT_PX",
    "APP_FONT_PT", "CONTENT_PAD_PX", "stylesheet", "palette", "apply_theme",
    "install_translations", "ui_language",
    # Qt 原文 → 中文的补缺表（另有 `submit_panel.QT_PREVIEW_MENU_ZH`，键同样是 Qt 原文）
    "QT_EDIT_MENU_ZH", "QT_STANDARD_BUTTON_ZH",
    "font_family", "set_font_family", "apply_font", "font_file", "load_font_file",
    "font_stack", "font_families",
    "quiet_label_style", "muted_label_style", "color_style", "mark_accent",
    "mark_flat", "mark_danger", "mono_font", "ui_font", "elide",
    "BASE_WINDOW_W", "BASE_WINDOW_H", "SCALE_MIN", "SCALE_MAX", "SCALE_STEP",
    "MIN_FONT_PX", "scale", "scale_for", "set_scale", "font_px", "scale_font",
    "apply_tracked_font", "rescale",
]
