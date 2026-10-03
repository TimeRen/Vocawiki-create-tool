"""「样式」标签页：原 html/css-tag-editor.html 的 PyQt5 版。

三段（Songbox / Introduction / 歌词）+ 四种模板目标共用右边同一套控件；
状态与文本生成全在 utils/ui/style_state.py，这里只负责控件读写与预览。

- 「保存并继续」把完整参数文本 + 「使用 LyricsKai/hover」开关交回流程（utils/color_editor.parse_color_wiki）。
- 「取消」不返回任何内容（等于没编辑过）。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.color_editor import EditorApi
from utils.ui import aux_tools, theme
from utils.ui import style_state
from utils.ui.aux_tools import AuxState
from utils.ui.style_preview import StylePreview
from utils.ui.widgets import CollapsibleBox, ColorField, CoverView, SectionTabs
from utils.ui.workers import FunctionWorker

# 「Wikitext 参数」框头上的那行字：手改过之后换成第二句，告诉用户内容不会被冲掉
WIKI_LABEL = "Wikitext 参数（可直接改，改完点「从文本载入」）"
WIKI_LABEL_DIRTY = "Wikitext 参数（手改的内容会留着 · 点「从文本载入」让它生效）"
WIKI_LABEL_UNKNOWN = "Wikitext 参数（没认出参数 · 需要「|颜色1 = …」这样的行）"
CODE_LABEL = "完整 CSS（可编辑）"
CODE_LABEL_DIRTY = "完整 CSS（手改的内容会留着 · 点「应用代码」让它生效）"


def _spin(minimum: float, maximum: float, step: float = 1, decimals: int = 0,
          suffix: str = "", callback=None) -> QtWidgets.QAbstractSpinBox:
    """统一的小数字输入框（decimals=0 用整型）。"""
    box: QtWidgets.QAbstractSpinBox
    if decimals == 0:
        box = QtWidgets.QSpinBox()
        box.setRange(int(minimum), int(maximum))
        box.setSingleStep(int(step))
    else:
        box = QtWidgets.QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setSingleStep(step)
        box.setDecimals(decimals)
    if suffix:
        box.setSuffix(suffix)
    # 宽度别太大也别太窄：固定宽度会让「角度 / 中心 X / Y」这类多控件行溢出（右栏只有 ~400px）
    box.setMinimumWidth(76)
    box.setMaximumWidth(110)
    if callback is not None:
        box.valueChanged.connect(lambda _value: callback())
    return box


def _label(text: str, parent=None, width: int = 64) -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text, parent)
    widget.setMinimumWidth(width)
    return widget


def _clear_layout(layout: QtWidgets.QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _set_pick_active(field: Optional["ColorField"], active: bool) -> None:
    """给取色器开关「吸管」高亮。

    ⚠️ 这个控件**可能已经被删掉**（图层 / 阴影那一行重建时 `deleteLater()` 了），
    那时操作 C++ 对象会抛 `RuntimeError: wrapped C/C++ object … has been deleted` ——
    在槽里抛出去就是「界面出错」且这次点击作废（用户 2026-10-03 报的：加完图层点保存
    「显示界面出错而无法保存」）。这种时候直接忽略就行。
    """
    if field is None:
        return
    try:
        field.set_pick_active(active)
    except RuntimeError:                     # 控件已经没了，没什么可复原的
        logging.debug("取色器已被重建 / 删除，忽略吸管状态")


class StylePanel(QtWidgets.QWidget):
    """样式编辑器页（主窗口里的一页）。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()

    # Songbox 段上次编辑的对象（切回 Songbox 段时恢复），-1 表示「全局」
    _last_songbox_target: Any = -1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.states: List[Dict[str, Any]] = [style_state.blank_state() for _ in range(3)]
        self.gstate: Dict[str, Any] = style_state.blank_state()
        self.tpl_states: Dict[str, Dict[str, Any]] = style_state.tpl_default_states()
        self.current: Any = -1
        self.section = "songbox"
        self.view = "all"
        self._defaults = style_state.blank_state()
        self._tpl_defaults = style_state.tpl_default_states()
        self._loading = False
        # 「Wikitext 参数」框被手改过没有：切右侧的标签 / 改控件都不许冲掉用户写的内容
        self._wiki_dirty = False
        self._writing_wiki = False        # 正在由程序写这个框（别把手改标记点亮）
        # 「完整 CSS」框里手改但还没点「应用代码」的内容，按编辑对象分开存
        self._code_dirty: Dict[str, str] = {}
        self._writing_code = False
        self._pick_field: Optional[ColorField] = None
        self._cover_path: Optional[Path] = None
        self._ai_api = EditorApi()
        # 辅助工具（测量 / 参照物）：状态共用一份，预览台与封面各记各的坐标
        self.aux = AuxState()
        self._ai_undo: Optional[tuple] = None
        self._ai_worker = None
        self._ai_requested: List[str] = []         # 本次请求过的对象 id（只应用这些）
        self._ai_context: Dict[str, Any] = {}      # `_load_ai_context()` 填；构造期 _select 会读
        # 「补充要求」里自动填进去的那份提示词；None = 用户手写过，别再自动覆盖
        self._ai_note_auto: Optional[str] = ""
        self._dynamic_boxes: Dict[str, QtWidgets.QVBoxLayout] = {}
        self._build_ui()
        self._select(-1, confirm=False, section="songbox")

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(10)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)
        root.addLayout(left, 5)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        # 右栏控件（数字框 + 取色器）本身就宽：3:2 时右栏只有 ~390px，比控件的最小宽度
        # 还窄 → 每一行右边都被裁掉一截（数字框的上下箭头都看不全），所以给到 5:4。
        root.addLayout(right, 4)

        # —— 左：封面 + 预览 + 文本 ——
        cover_row = QtWidgets.QHBoxLayout()
        self.cover_view = CoverView(self)
        self.cover_view.set_aux(self.aux, "cover")
        self.cover_view.picked.connect(self._on_cover_picked)
        self.cover_view.aux_changed.connect(self._on_aux_changed)
        self.cover_view.aux_exit.connect(self._on_aux_exit)
        cover_row.addWidget(self.cover_view, 1)
        cover_buttons = QtWidgets.QVBoxLayout()
        self.import_button = QtWidgets.QPushButton("导入图片", self)
        self.import_button.clicked.connect(self._import_cover)
        cover_buttons.addWidget(self.import_button)
        self.clear_cover_button = QtWidgets.QPushButton("移除图片", self)
        self.clear_cover_button.clicked.connect(self._clear_cover)
        cover_buttons.addWidget(self.clear_cover_button)
        cover_buttons.addStretch(1)
        cover_row.addLayout(cover_buttons)
        left.addLayout(cover_row)

        self.preview = StylePreview(self)
        self.preview.set_aux(self.aux, "preview")
        self.preview.aux_changed.connect(self._on_aux_changed)
        self.preview.aux_exit.connect(self._on_aux_exit)
        # 套一层滚动区：画布宽 / 间距调大时宁可出滚动条，也不要把第三块裁掉
        self.preview_scroll = QtWidgets.QScrollArea(self)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.preview_scroll.setWidget(self.preview)
        # 别让别的块把它挤没了（小窗口下最低也要能看见色块 + Introduction）；
        # 也不让它无限长高（内容不会缩放，再高也是空白）——多出来的高度留给下面的参数框
        self.preview_scroll.setMinimumHeight(200)
        self.preview_scroll.setMaximumHeight(380)
        left.addWidget(self.preview_scroll)

        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(_label("显示范围", self))
        self.view_combo = QtWidgets.QComboBox(self)
        for key, label in (("all", "全部"), ("songbox", "Songbox"),
                           ("intro", "Introduction"), ("lyrics", "歌词")):
            self.view_combo.addItem(label, key)
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        view_row.addWidget(self.view_combo)
        view_row.addWidget(_label("画布宽", self, 50))
        self.canvas_spin = _spin(320, 820, 10, 0)
        # 默认 520：左栏（5/9 宽）装得下 520 + 20 的内边距，默认状态不用横向滚动
        self.canvas_spin.setValue(520)
        view_row.addWidget(self.canvas_spin)
        view_row.addWidget(_label("间距", self, 34))
        self.gap_spin = _spin(0, 40, 1, 0)
        self.gap_spin.setValue(22)
        view_row.addWidget(self.gap_spin)
        # 两个滑块都建好之后再接信号（否则 setValue 时会摸到还没创建的控件）
        self.canvas_spin.valueChanged.connect(lambda _value: self._refresh_preview())
        self.gap_spin.valueChanged.connect(lambda _value: self._refresh_preview())
        view_row.addStretch(1)
        left.addLayout(view_row)

        self.wiki_label = _label(WIKI_LABEL, self, 0)
        left.addWidget(self.wiki_label)
        self.wiki_edit = QtWidgets.QPlainTextEdit(self)
        self.wiki_edit.setMinimumHeight(96)
        self.wiki_edit.setMaximumHeight(400)
        self.wiki_edit.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.wiki_edit, theme.MONO_SIZE_PX, mono=True)
        self.wiki_edit.textChanged.connect(self._on_wiki_edited)
        left.addWidget(self.wiki_edit, 1)          # 窗口高时参数框跟着长（看得更多）
        wiki_buttons = QtWidgets.QHBoxLayout()
        self.load_wiki_button = QtWidgets.QPushButton("从文本载入", self)
        self.load_wiki_button.clicked.connect(self._load_from_wiki)
        wiki_buttons.addWidget(self.load_wiki_button)
        self.copy_wiki_button = QtWidgets.QPushButton("复制", self)
        self.copy_wiki_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(self.wiki_edit.toPlainText()))
        wiki_buttons.addWidget(self.copy_wiki_button)
        wiki_buttons.addStretch(1)
        left.addLayout(wiki_buttons)

        self.code_box = CollapsibleBox(CODE_LABEL, expanded=False, parent=self)
        self.code_edit = QtWidgets.QPlainTextEdit(self.code_box.content)
        self.code_edit.setMinimumHeight(120)
        self.code_edit.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.code_edit, theme.MONO_SIZE_PX, mono=True)
        self.code_edit.textChanged.connect(self._on_code_edited)
        self.code_box.add(self.code_edit)
        code_buttons = QtWidgets.QWidget(self.code_box.content)
        code_row = QtWidgets.QHBoxLayout(code_buttons)
        code_row.setContentsMargins(0, 0, 0, 0)
        apply_code = QtWidgets.QPushButton("应用代码", code_buttons)
        apply_code.clicked.connect(self._apply_code)
        code_row.addWidget(apply_code)
        code_row.addStretch(1)
        self.code_box.add(code_buttons)
        left.addWidget(self.code_box)

        # —— 右：控件面板 ——
        right.addWidget(_label("编辑对象", self, 0))
        self.section_tabs = SectionTabs([(key, spec["label"])
                                         for key, spec in style_state.SECTIONS.items()], self)
        self.section_tabs.section_changed.connect(self._on_section_changed)
        self.section_tabs.target_changed.connect(self._on_target_changed)
        right.addWidget(self.section_tabs)
        self.section_tip = QtWidgets.QLabel("", self)
        self.section_tip.setWordWrap(True)
        self.section_tip.setStyleSheet("QLabel { color: #54595d; }")
        right.addWidget(self.section_tip)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        holder = QtWidgets.QWidget(scroll)
        self.panel_layout = QtWidgets.QVBoxLayout(holder)
        self.panel_layout.setContentsMargins(0, 0, 6, 0)
        self.panel_layout.setSpacing(2)
        scroll.setWidget(holder)
        right.addWidget(scroll, 1)

        self._build_switches()
        self._build_text_box()
        self._build_font_box()
        self._build_background_box()
        self._build_border_box()
        self._build_shadows_box()
        self._build_extras_box()
        self._build_aux_box()
        self._build_ai_box()
        self.panel_layout.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        self.reset_one_button = QtWidgets.QPushButton("重置当前", self)
        self.reset_one_button.clicked.connect(self._reset_current)
        buttons.addWidget(self.reset_one_button)
        self.reset_all_button = QtWidgets.QPushButton("重置全部", self)
        self.reset_all_button.clicked.connect(self._reset_all)
        buttons.addWidget(self.reset_all_button)
        buttons.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.cancel_button)
        self.save_button = QtWidgets.QPushButton("保存并继续", self)
        self.save_button.setDefault(True)
        theme.mark_accent(self.save_button)
        self.save_button.clicked.connect(self._on_save)
        buttons.addWidget(self.save_button)
        right.addLayout(buttons)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    # —— 几个分组 ——
    def _build_switches(self) -> None:
        self.switch_box = CollapsibleBox("开关", expanded=True, parent=self)
        self.enabled_check = QtWidgets.QCheckBox("在 Wikitext 里输出该参数", self.switch_box.content)
        self.enabled_check.toggled.connect(self._on_enabled_toggled)
        self.switch_box.add(self.enabled_check)
        self.hover_check = QtWidgets.QCheckBox("使用 LyricsKai/hover（悬停显示译文）",
                                               self.switch_box.content)
        self.switch_box.add(self.hover_check)
        self.panel_layout.addWidget(self.switch_box)

    def _build_text_box(self) -> None:
        self.text_box = CollapsibleBox("内容与尺寸", expanded=True, parent=self)
        body = self.text_box
        self.text_edit = QtWidgets.QLineEdit(body.content)
        self.text_edit.textChanged.connect(self._on_text_changed)
        body.add_row("标签文字", self.text_edit)
        self.width_spin = _spin(20, 100, 1, 0, "%", self._on_widgets_changed)
        body.add_row("宽度", self.width_spin)
        self.max_width_spin = _spin(120, 760, 2, 0, "px", self._on_widgets_changed)
        body.add_row("最大宽度", self.max_width_spin)
        self.height_spin = _spin(16, 80, 1, 0, "px", self._on_widgets_changed)
        body.add_row("高度", self.height_spin)
        padding = QtWidgets.QWidget(body.content)
        padding_row = QtWidgets.QHBoxLayout(padding)
        padding_row.setContentsMargins(0, 0, 0, 0)
        self.pad_x_spin = _spin(0, 60, 1, 0, "px", self._on_widgets_changed)
        self.pad_y_spin = _spin(0, 30, 1, 0, "px", self._on_widgets_changed)
        padding_row.addWidget(_label("左右", padding, 34))
        padding_row.addWidget(self.pad_x_spin)
        padding_row.addWidget(_label("上下", padding, 34))
        padding_row.addWidget(self.pad_y_spin)
        padding_row.addStretch(1)
        body.add_row("内边距", padding)
        self.radius_spin = _spin(0, 100, 1, 0, "px", self._on_widgets_changed)
        body.add_row("圆角", self.radius_spin)
        align_row = QtWidgets.QWidget(body.content)
        align_layout = QtWidgets.QHBoxLayout(align_row)
        align_layout.setContentsMargins(0, 0, 0, 0)
        self.align_check = QtWidgets.QCheckBox("垂直居中", align_row)
        self.align_check.toggled.connect(self._on_widgets_changed)
        align_layout.addWidget(self.align_check)
        self.box_sizing_check = QtWidgets.QCheckBox("border-box", align_row)
        self.box_sizing_check.toggled.connect(self._on_widgets_changed)
        align_layout.addWidget(self.box_sizing_check)
        align_layout.addStretch(1)
        body.add(align_row)
        self.panel_layout.addWidget(body)

    def _build_font_box(self) -> None:
        self.font_box = CollapsibleBox("文字", expanded=True, parent=self)
        body = self.font_box
        auto_row = QtWidgets.QWidget(body.content)
        auto_layout = QtWidgets.QHBoxLayout(auto_row)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        auto_layout.addWidget(_label("自动文字色", auto_row))
        self.fg_threshold_spin = _spin(0, 100, 1, 0, "", None)
        self.fg_threshold_spin.setValue(60)
        auto_layout.addWidget(self.fg_threshold_spin)
        self.auto_fg_button = QtWidgets.QPushButton("按底色算", auto_row)
        self.auto_fg_button.setToolTip("按底色亮度（阈值取自左边）自动选黑字或白字")
        self.auto_fg_button.clicked.connect(self._auto_fg)
        auto_layout.addWidget(self.auto_fg_button)
        auto_layout.addStretch(1)
        body.add(auto_row)
        self.color_field = ColorField(parent=body.content)
        self.color_field.changed.connect(self._on_widgets_changed)
        self.color_field.pick_requested.connect(self._start_pick)
        body.add_row("文字颜色", self.color_field)
        self.font_size_spin = _spin(8, 32, 0.5, 1, "px", self._on_widgets_changed)
        body.add_row("字号", self.font_size_spin)
        self.line_height_spin = _spin(0.8, 2.6, 0.05, 2, "", self._on_widgets_changed)
        body.add_row("行高", self.line_height_spin)
        self.weight_combo = QtWidgets.QComboBox(body.content)
        for weight in style_state.FONT_WEIGHTS:
            self.weight_combo.addItem(str(weight), weight)
        self.weight_combo.currentIndexChanged.connect(self._on_widgets_changed)
        body.add_row("字重", self.weight_combo)
        self.letter_spacing_spin = _spin(0, 6, 0.1, 1, "px", self._on_widgets_changed)
        body.add_row("字距", self.letter_spacing_spin)
        self.opacity_spin = _spin(0.1, 1, 0.01, 2, "", self._on_widgets_changed)
        body.add_row("不透明度", self.opacity_spin)
        self.panel_layout.addWidget(body)

    def _build_background_box(self) -> None:
        self.background_box = CollapsibleBox("背景", expanded=True, parent=self)
        body = self.background_box
        self.bg_field = ColorField(parent=body.content)
        self.bg_field.changed.connect(self._on_widgets_changed)
        self.bg_field.pick_requested.connect(self._start_pick)
        body.add_row("底色", self.bg_field)
        self.layers_layout = QtWidgets.QVBoxLayout()
        holder = QtWidgets.QWidget(body.content)
        holder.setLayout(self.layers_layout)
        body.add(holder)
        add_layer = QtWidgets.QPushButton("添加渐变图层", body.content)
        add_layer.clicked.connect(self._add_layer)
        body.add(add_layer)
        self.panel_layout.addWidget(body)

    def _build_border_box(self) -> None:
        self.border_box = CollapsibleBox("边框", expanded=False, parent=self)
        body = self.border_box
        self.border_width_spin = _spin(0, 10, 0.5, 1, "px", self._on_widgets_changed)
        body.add_row("宽度", self.border_width_spin)
        self.border_style_combo = QtWidgets.QComboBox(body.content)
        for name in style_state.BORDER_STYLES:
            self.border_style_combo.addItem(name, name)
        self.border_style_combo.currentIndexChanged.connect(self._on_widgets_changed)
        body.add_row("线型", self.border_style_combo)
        self.border_current_check = QtWidgets.QCheckBox("与文字同色（currentColor）", body.content)
        self.border_current_check.toggled.connect(self._on_border_current_toggled)
        body.add(self.border_current_check)
        self.border_field = ColorField(parent=body.content)
        self.border_field.changed.connect(self._on_widgets_changed)
        self.border_field.pick_requested.connect(self._start_pick)
        body.add_row("边框色", self.border_field)
        self.panel_layout.addWidget(body)

    def _build_shadows_box(self) -> None:
        self.shadow_box = CollapsibleBox("阴影", expanded=False, parent=self)
        body = self.shadow_box
        self.box_shadows_layout = QtWidgets.QVBoxLayout()
        holder = QtWidgets.QWidget(body.content)
        holder.setLayout(self.box_shadows_layout)
        body.add(_label("外阴影", body.content, 0))
        body.add(holder)
        add_box = QtWidgets.QPushButton("添加外阴影", body.content)
        add_box.clicked.connect(self._add_box_shadow)
        body.add(add_box)
        self.text_shadows_layout = QtWidgets.QVBoxLayout()
        holder2 = QtWidgets.QWidget(body.content)
        holder2.setLayout(self.text_shadows_layout)
        body.add(_label("文字阴影", body.content, 0))
        body.add(holder2)
        add_text = QtWidgets.QPushButton("添加文字阴影", body.content)
        add_text.clicked.connect(self._add_text_shadow)
        body.add(add_text)
        self.panel_layout.addWidget(body)

    def _build_extras_box(self) -> None:
        self.extras_box = CollapsibleBox("其他声明", expanded=False, parent=self)
        self.extras_edit = QtWidgets.QPlainTextEdit(self.extras_box.content)
        self.extras_edit.setMinimumHeight(72)
        self.extras_edit.setPlaceholderText("每行一条，例如 transform: rotate(-2deg);")
        self.extras_edit.textChanged.connect(self._on_extras_changed)
        self.extras_box.add(self.extras_edit)
        self.panel_layout.addWidget(self.extras_box)

    def _build_aux_box(self) -> None:
        """辅助工具：在预览台 / 封面上量距离、放参照物（不影响输出，只是看尺寸用）。"""
        self.aux_box = CollapsibleBox("辅助工具（测量 / 参照物）", expanded=False, parent=self)
        body = self.aux_box

        measure_row = QtWidgets.QWidget(body.content)
        measure_layout = QtWidgets.QHBoxLayout(measure_row)
        measure_layout.setContentsMargins(0, 0, 0, 0)
        self.measure_button = QtWidgets.QPushButton("开始测量", measure_row)
        self.measure_button.setCheckable(True)
        self.measure_button.setToolTip("在预览台或封面上点两下量出距离与 Δx / Δy；"
                                       "Esc 或右键退出")
        self.measure_button.clicked.connect(self._on_measure_clicked)
        measure_layout.addWidget(self.measure_button)
        self.measure_clear_button = QtWidgets.QPushButton("清除测量", measure_row)
        self.measure_clear_button.clicked.connect(self._clear_measure)
        measure_layout.addWidget(self.measure_clear_button)
        measure_layout.addStretch(1)
        body.add(measure_row)
        self.measure_label = QtWidgets.QLabel(aux_tools.AuxState().measure_text(), body.content)
        self.measure_label.setWordWrap(True)
        self.measure_label.setStyleSheet("QLabel { color: #2f80ff; }")
        body.add(self.measure_label)

        guide_row = QtWidgets.QWidget(body.content)
        guide_layout = QtWidgets.QHBoxLayout(guide_row)
        guide_layout.setContentsMargins(0, 0, 0, 0)
        self.guide_button = QtWidgets.QPushButton("开始放置", guide_row)
        self.guide_button.setCheckable(True)
        self.guide_button.setToolTip("按下面的设置，在预览台或封面上点一下放一个参照物；"
                                     "Esc 或右键退出")
        self.guide_button.clicked.connect(self._on_guide_clicked)
        guide_layout.addWidget(self.guide_button)
        self.guide_kind_combo = QtWidgets.QComboBox(guide_row)
        for kind in aux_tools.GUIDE_KINDS:
            self.guide_kind_combo.addItem(aux_tools.GUIDE_KIND_LABELS[kind], kind)
        self.guide_kind_combo.currentIndexChanged.connect(self._sync_guide_settings)
        guide_layout.addWidget(self.guide_kind_combo)
        self.guide_clear_button = QtWidgets.QPushButton("清空参照物", guide_row)
        self.guide_clear_button.clicked.connect(self._clear_guides)
        guide_layout.addWidget(self.guide_clear_button)
        guide_layout.addStretch(1)
        body.add(guide_row)

        size_row = QtWidgets.QWidget(body.content)
        size_layout = QtWidgets.QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(_label("角度", size_row, 34))
        self.guide_angle_spin = _spin(0, 359, 1, 0, "°")
        self.guide_angle_spin.setValue(int(self.aux.settings["angle"]))
        self.guide_angle_spin.valueChanged.connect(self._sync_guide_settings)
        size_layout.addWidget(self.guide_angle_spin)
        size_layout.addWidget(_label("宽", size_row, 20))
        self.guide_w_spin = _spin(20, 900, 1, 0, "px")
        self.guide_w_spin.setValue(int(self.aux.settings["w"]))
        self.guide_w_spin.valueChanged.connect(self._sync_guide_settings)
        size_layout.addWidget(self.guide_w_spin)
        size_layout.addWidget(_label("高", size_row, 20))
        self.guide_h_spin = _spin(20, 900, 1, 0, "px")
        self.guide_h_spin.setValue(int(self.aux.settings["h"]))
        self.guide_h_spin.valueChanged.connect(self._sync_guide_settings)
        size_layout.addWidget(self.guide_h_spin)
        size_layout.addStretch(1)
        body.add(size_row)
        self.guide_label = QtWidgets.QLabel(self.aux.guide_text(), body.content)
        self.guide_label.setWordWrap(True)
        self.guide_label.setStyleSheet("QLabel { color: #ff5a7a; }")
        body.add(self.guide_label)
        self._sync_guide_settings()
        self.panel_layout.addWidget(self.aux_box)

    # ------------------------------------------------------------ 辅助工具

    def _on_measure_clicked(self) -> None:
        self._set_aux_mode("measure" if self.measure_button.isChecked() else None)

    def _on_guide_clicked(self) -> None:
        self._set_aux_mode("guide" if self.guide_button.isChecked() else None)

    def _set_aux_mode(self, mode: Optional[str]) -> None:
        """切换辅助工具模式（与吸管取色互斥）；传 None 表示退出。"""
        if mode is not None:
            self._stop_pick()
        if self.aux.mode == mode:
            mode = None
        self.aux.set_mode(mode)
        self.measure_button.setChecked(self.aux.mode == "measure")
        self.guide_button.setChecked(self.aux.mode == "guide")
        cursor = QtCore.Qt.CrossCursor if self.aux.mode else QtCore.Qt.ArrowCursor
        self.preview.setCursor(cursor)
        self.cover_view.setCursor(cursor)
        self._refresh_aux()

    def _on_aux_changed(self, status: str) -> None:
        if status == aux_tools.CLICK_FULL:
            self.guide_label.setText(f"参照物最多 {aux_tools.MAX_GUIDES} 个，先点「清空参照物」再加")
            return
        self._refresh_aux()

    def _on_aux_exit(self) -> None:
        self._set_aux_mode(None)

    def _clear_measure(self) -> None:
        self.aux.clear_measure()
        self._refresh_aux()

    def _clear_guides(self) -> None:
        self.aux.clear_guides()
        self._refresh_aux()

    def _sync_guide_settings(self) -> None:
        self.aux.settings["kind"] = self.guide_kind_combo.currentData() or "line"
        self.aux.settings["angle"] = self.guide_angle_spin.value()
        self.aux.settings["w"] = self.guide_w_spin.value()
        self.aux.settings["h"] = self.guide_h_spin.value()
        self.guide_h_spin.setEnabled(self.aux.settings["kind"] == "rect")

    def _refresh_aux(self) -> None:
        self.measure_label.setText(self.aux.measure_text())
        self.guide_label.setText(self.aux.guide_text())
        self.preview.update()
        self.cover_view.update()

    def _build_ai_box(self) -> None:
        self.ai_box = CollapsibleBox("AI 参考封面生成 CSS", expanded=False, parent=self)
        body = self.ai_box
        scope_row = QtWidgets.QWidget(body.content)
        scope_layout = QtWidgets.QHBoxLayout(scope_row)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.addWidget(_label("范围", scope_row, 34))
        self.ai_scope_combo = QtWidgets.QComboBox(scope_row)
        self.ai_scope_combo.addItem("当前编辑对象", "cur")
        self.ai_scope_combo.addItem("全部（Songbox + Introduction + 歌词）", "all")
        self.ai_scope_combo.currentIndexChanged.connect(self._on_ai_scope_changed)
        scope_layout.addWidget(self.ai_scope_combo, 1)
        body.add(scope_row)
        self.ai_note_edit = QtWidgets.QPlainTextEdit(body.content)
        self.ai_note_edit.setMinimumHeight(64)
        self.ai_note_edit.setPlaceholderText("补充要求（留空则用 config.yaml 里的默认提示词）")
        self.ai_note_edit.textChanged.connect(self._on_ai_note_edited)
        body.add(self.ai_note_edit)
        self.ai_color_only_check = QtWidgets.QCheckBox("只改颜色（不写尺寸 / 字号 / 间距）",
                                                       body.content)
        self.ai_color_only_check.setChecked(False)      # 默认不勾选（用户 2026-09 要求）
        body.add(self.ai_color_only_check)
        ai_buttons = QtWidgets.QWidget(body.content)
        ai_row = QtWidgets.QHBoxLayout(ai_buttons)
        ai_row.setContentsMargins(0, 0, 0, 0)
        self.ai_button = QtWidgets.QPushButton("AI 参考封面生成 CSS", ai_buttons)
        self.ai_button.clicked.connect(self._run_ai)
        ai_row.addWidget(self.ai_button)
        self.ai_undo_button = QtWidgets.QPushButton("撤销这次生成", ai_buttons)
        self.ai_undo_button.setVisible(False)
        self.ai_undo_button.clicked.connect(self._undo_ai)
        ai_row.addWidget(self.ai_undo_button)
        ai_row.addStretch(1)
        body.add(ai_buttons)
        self.ai_tip = QtWidgets.QLabel("", body.content)
        self.ai_tip.setWordWrap(True)
        self.ai_tip.setStyleSheet("QLabel { color: #54595d; }")
        body.add(self.ai_tip)
        self.panel_layout.addWidget(self.ai_box)

    # ------------------------------------------------------------ 启动

    def start(self, payload: Dict[str, Any]) -> None:
        """流程把这一页点亮时调用：载入初始文本、封面与开关状态。"""
        initial = (payload or {}).get("initial") or ""
        if initial.strip():
            states, tpl_states = style_state.parse_wiki_text(initial)
            self.states = states
            self.gstate = style_state.copy_state(states[0])
            self.tpl_states = tpl_states
        self.hover_check.setChecked(bool((payload or {}).get("hover")))
        self._cover_path = None
        cover = (payload or {}).get("cover")
        if cover:
            self._load_cover(Path(cover))
        self._forget_wiki_edits()          # 换一首歌：上一首里手改的内容不该带过来
        self._forget_code_edits()
        self._select(-1, confirm=False, section="songbox")
        self._load_ai_context()
        self._reset_ai_options()            # 换一首歌：范围回到「当前编辑对象」
        # 换一首歌就清掉上一首的测量 / 参照物
        self.aux.clear_measure()
        self.aux.clear_guides()
        self._set_aux_mode(None)
        self.setFocus()

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录 → 重新开始」时由主窗口调）。

        三块颜色状态回到默认（`_reset_all` 会一并刷新 wikitext / CSS / 预览），
        封面与上一首歌的测量 / 参照物也一起清掉。
        """
        self._cover_path = None
        self._ai_api.set_cover_image(None)
        self._reset_all()
        self.aux.clear_measure()
        self.aux.clear_guides()
        self._set_aux_mode(None)

    def _load_cover(self, path: Optional[Path]) -> None:
        if path is None or not self.cover_view.load_file(path):
            return
        self._cover_path = Path(path)
        self._ai_api.set_cover_image(self._cover_path)

    def _import_cover(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择封面图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif)")
        if path:
            self._load_cover(Path(path))

    def _clear_cover(self) -> None:
        self._cover_path = None
        self._ai_api.set_cover_image(None)
        self.cover_view.clear_image()
        self._stop_pick()

    # ------------------------------------------------------------ 选中对象

    def _on_section_changed(self, section: str) -> None:
        self.section = section
        if section == "songbox":
            target = self._last_songbox_target
        elif section == "intro":
            target = "introLabel"
        else:
            target = "lyrContainer"
        self._select(target, confirm=False, section=section)

    def _on_target_changed(self, target: Any) -> None:
        if isinstance(target, str) and target.isdigit():
            target = int(target)
        if target == -1:
            self._select(-1, confirm=True, section="songbox")
            return
        self._select(target, confirm=False, section=self.section)
        if isinstance(target, int):
            self._last_songbox_target = target

    def _section_targets(self, section: str) -> List[Tuple[Any, str]]:
        if section == "songbox":
            return [("-1", "全局"), ("0", style_state.RECT_LABELS[0]),
                    ("1", style_state.RECT_LABELS[1]), ("2", style_state.RECT_LABELS[2])]
        if section == "intro":
            return [("introLabel", "标签格")]
        return [("lyrContainer", "容器"), ("lyrOrig", "原文"), ("lyrTrans", "译文")]

    def _select(self, target: Any, confirm: bool = False, section: Optional[str] = None) -> None:
        if isinstance(target, str) and target.lstrip("-").isdigit():
            target = int(target)
        if confirm and self.current != -1:
            answer = QtWidgets.QMessageBox.question(
                self, "切换至全局编辑",
                "此时切换至全局编辑会将该栏样式应用至全局，确定继续吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                self._sync_tabs()
                return
        if section:
            self.section = section
        elif isinstance(target, str):
            self.section = style_state.TPL_TARGETS[target]["section"]
        self.current = target
        self._sync_tabs()
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_preview()
        # 「补充要求」跟着当前对象换默认提示词（Songbox / Introduction / 歌词 各一份）
        self._update_ai_note()

    def _sync_tabs(self) -> None:
        self.section_tabs.set_section(self.section)
        self.section_tabs.set_targets(self._section_targets(self.section), self._target_key())
        self.section_tip.setText(style_state.SECTIONS[self.section]["tip"])

    def _target_key(self) -> str:
        return str(self.current)

    def state(self) -> Dict[str, Any]:
        """当前编辑对象的状态（矩形 / 全局 / 模板目标）。"""
        if isinstance(self.current, str):
            return self.tpl_states[self.current]
        if self.current == -1:
            return self.gstate
        return self.states[self.current]

    # ------------------------------------------------------------ 状态 → 控件

    def _load_current(self) -> None:
        state = self.state()
        self._loading = True
        try:
            is_rect = isinstance(self.current, int)
            is_tpl = isinstance(self.current, str)
            spec = style_state.TPL_TARGETS.get(self.current) if is_tpl else None
            self.text_edit.setEnabled(is_rect)
            self.text_edit.setText(state.get("text") or
                                   (style_state.RECT_LABELS[self.current] if is_rect else ""))
            if is_rect and self.current == -1:
                self.text_edit.setEnabled(False)
                self.text_edit.setPlaceholderText("全局模式下文本保持各自独立")
            else:
                self.text_edit.setPlaceholderText("预览里的标签文字（不影响输出）")
            self.width_spin.setValue(int(state.get("width", 100)))
            self.max_width_spin.setValue(int(state.get("maxWidth", 450)))
            self.height_spin.setValue(int(state.get("height", 24)))
            self.pad_x_spin.setValue(int(state.get("padX", 0)))
            self.pad_y_spin.setValue(int(state.get("padY", 0)))
            self.radius_spin.setValue(int(state.get("radius", 0)))
            self.align_check.setChecked(bool(state.get("alignCenter")))
            self.box_sizing_check.setChecked(bool(state.get("boxSizing", True)))
            self.color_field.set_value(state.get("color"), state.get("colorAlpha", 1.0))
            self.font_size_spin.setValue(float(state.get("fontSize", 12)))
            self.line_height_spin.setValue(float(state.get("lineHeight", 1)))
            index = self.weight_combo.findData(int(state.get("weight", 400)))
            self.weight_combo.setCurrentIndex(max(0, index))
            self.letter_spacing_spin.setValue(float(state.get("letterSpacing", 0)))
            self.opacity_spin.setValue(float(state.get("opacity", 1)))
            self.bg_field.set_value(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0))
            self.border_width_spin.setValue(float(state.get("borderWidth", 0)))
            style_index = self.border_style_combo.findData(state.get("borderStyle") or "solid")
            self.border_style_combo.setCurrentIndex(max(0, style_index))
            self.border_current_check.setChecked(bool(state.get("borderCurrent", True)))
            self.border_field.set_value(state.get("borderColor"), state.get("borderAlpha", 1.0))
            self.border_field.setEnabled(not state.get("borderCurrent", True))
            self.extras_edit.setPlainText("\n".join(state.get("extras") or []))
            self.enabled_check.setChecked(bool(state.get("enabled", True)))
            self.switch_box.setVisible(bool(is_tpl))
            self.enabled_check.setVisible(bool(spec and spec["toggle"]))
            self.switch_box.content.setVisible(bool(is_tpl))
            self.hover_check.setVisible(self.section == "lyrics")
            self._show_code()
            self._set_wiki_text(self._wiki_text())
        finally:
            self._loading = False

    def _code_text(self) -> str:
        if isinstance(self.current, str):
            spec = style_state.TPL_TARGETS[self.current]
            return style_state.code_css(f".{spec['param']}", self.state(), spec["label"])
        if self.current == -1:
            parts = [style_state.code_css(f".{style_state.RECT_CLASSES[i]}", self.gstate,
                                          style_state.RECT_LABELS[i] + "（全局）")
                     for i in range(3)]
            return "\n\n".join(parts)
        return style_state.code_css(f".{style_state.RECT_CLASSES[self.current]}", self.state(),
                                    style_state.RECT_LABELS[self.current])

    def _wiki_text(self) -> str:
        return style_state.full_wiki_text(self.states, self.tpl_states)

    # ------------------------------------------------------------ 控件 → 状态

    def _on_widgets_changed(self) -> None:
        if self._loading:
            return
        try:
            self._apply_widgets()
        except Exception as e:                       # noqa: BLE001 - 槽里不能往外抛
            logging.error("应用样式控件失败：%s", e, exc_info=e)

    def _apply_widgets(self) -> None:
        state = self.state()
        is_rect = isinstance(self.current, int)
        if is_rect and self.current >= 0:
            state["text"] = self.text_edit.text()
        state["width"] = self.width_spin.value()
        state["maxWidth"] = self.max_width_spin.value()
        state["height"] = self.height_spin.value()
        state["padX"] = self.pad_x_spin.value()
        state["padY"] = self.pad_y_spin.value()
        state["radius"] = self.radius_spin.value()
        state["alignCenter"] = self.align_check.isChecked()
        state["boxSizing"] = self.box_sizing_check.isChecked()
        state["color"], state["colorAlpha"] = self.color_field.value()
        state["fontSize"] = self.font_size_spin.value()
        state["lineHeight"] = self.line_height_spin.value()
        state["weight"] = self.weight_combo.currentData()
        state["letterSpacing"] = self.letter_spacing_spin.value()
        state["opacity"] = self.opacity_spin.value()
        state["bgSolid"], state["bgSolidAlpha"] = self.bg_field.value()
        if not self.border_current_check.isChecked():
            state["borderColor"], state["borderAlpha"] = self.border_field.value()
        if is_rect and self.current == -1:
            # 全局态改完再同步进三个颜色块（同步在最后，免得刚改的东西被覆盖）
            self._propagate_global()
        self._refresh_outputs()

    def _on_text_changed(self, text: str) -> None:
        if self._loading:
            return
        state = self.state()
        state["text"] = text
        self._refresh_preview()

    def _on_enabled_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.state()["enabled"] = checked
        self._refresh_outputs()

    def _on_border_current_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        state = self.state()
        state["borderCurrent"] = checked
        self.border_field.setEnabled(not checked)
        self._refresh_outputs()

    def _on_extras_changed(self) -> None:
        if self._loading:
            return
        self.state()["extras"] = [line.strip() for line in
                                  self.extras_edit.toPlainText().splitlines() if line.strip()]
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _propagate_global(self) -> None:
        """全局编辑：把 gstate 同步进三个颜色块（文字与 force 不动）。"""
        for index, target in enumerate(self.states):
            text = target.get("text")
            force = target.get("force")
            for key, value in self.gstate.items():
                if key in ("text", "force"):
                    continue
                target[key] = style_state.copy_state({"v": value})["v"]
            target["text"] = text or style_state.RECT_LABELS[index]
            target["force"] = force

    # ------------------------------------------------------------ 文本 / 代码

    def _refresh_outputs(self) -> None:
        self._set_wiki_text(self._wiki_text())
        self._show_code()
        self._refresh_preview()

    # —— 「Wikitext 参数」框：用户手改的内容不许被程序冲掉 ——

    def _on_wiki_edited(self) -> None:
        """用户在参数框里敲了字（程序自己写的不算）。"""
        if self._loading or self._writing_wiki:
            return
        if self._wiki_dirty:
            return
        self._wiki_dirty = True
        self.wiki_label.setText(WIKI_LABEL_DIRTY)

    def _set_wiki_text(self, text: str, force: bool = False) -> None:
        """把生成的参数文本写回左下角那个框。

        **手改过就不动它**（用户 2026-09 报：「改完后切右侧的标签，写的内容被覆盖了」）：
        那个框是所有三块颜色 + 模板参数的总输出，切标签并不会让它变，重写只会把用户
        敲的字冲掉；改右边的控件同理（模型变了但框里是他写的东西）。
        点了「从文本载入」（把他的文本解析回模型）、重置、换一首歌时才会 force 重写。
        """
        if self._wiki_dirty and not force:
            return
        self._writing_wiki = True
        try:
            self.wiki_edit.setPlainText(text)
        finally:
            self._writing_wiki = False
        self._wiki_dirty = False
        self.wiki_label.setText(WIKI_LABEL)

    def _forget_wiki_edits(self) -> None:
        """丢掉「手改过」的标记（模型整个换掉了：载入 / 重置 / 换歌）。"""
        self._wiki_dirty = False
        self.wiki_label.setText(WIKI_LABEL)

    # —— 「完整 CSS」框：手改优先（按编辑对象分开记）——

    def _on_code_edited(self) -> None:
        if self._loading or self._writing_code:
            return
        self._code_dirty[self._target_key()] = self.code_edit.toPlainText()
        self.code_box.set_title(CODE_LABEL_DIRTY)

    def _write_code(self, text: str) -> None:
        """程序往 CSS 框里写字（这段时间里的 `textChanged` 不算用户手改）。"""
        self._writing_code = True
        try:
            self.code_edit.setPlainText(text)
        finally:
            self._writing_code = False

    def _show_code(self) -> None:
        """把当前编辑对象的 CSS 写回「完整 CSS」框。

        这个框是**按编辑对象**的，所以手改也按对象记：切到别的对象看别的 CSS，
        切回来自动把你写的那份还回来（用户 2026-09 报「切右侧的标签会覆盖掉我写的内容」）。
        点「应用代码」、载入、重置、换歌才会丢掉它。
        """
        key = self._target_key()
        if key in self._code_dirty:
            self._write_code(self._code_dirty[key])
            self.code_box.set_title(CODE_LABEL_DIRTY)
            return
        self._write_code(self._code_text())
        self.code_box.set_title(CODE_LABEL)

    def _forget_code_edits(self) -> None:
        """模型整个换掉了：所有对象上手改的 CSS 都不再算数。"""
        self._code_dirty.clear()
        self.code_box.set_title(CODE_LABEL)

    def _refresh_preview(self) -> None:
        # 槽里抛异常在 PyQt5 里会让整个进程 abort，这里兜住并记日志
        try:
            self.preview.update_states(self.states, self.tpl_states, self.view,
                                       self.canvas_spin.value(), self.gap_spin.value(),
                                       self.current)
            # 预览区高度不小于内容（画布宽/高度调大时内容变高，别让最后一段被切掉），
            # 再多给一条横向滚动条的高度（不然它一出现就把内容顶出一条竖向滚动条）；
            # 也不让它无上限地撑高左栏，超过 280 就靠滚动看
            self.preview_scroll.setMinimumHeight(
                min(self.preview.minimumSizeHint().height() + 22, 280))
        except Exception as e:                       # noqa: BLE001
            logging.error("刷新预览失败：%s", e, exc_info=e)

    def _on_view_changed(self) -> None:
        self.view = self.view_combo.currentData()
        self._refresh_preview()

    def _load_from_wiki(self) -> None:
        """把左下角框里的文本解析回模型 —— **不重写那个框**。

        用户 2026-09 报：「点『从文本载入』后我在框里改的内容被重置了」。旧实现解析完又用
        `self._wiki_text()` 把框重写了一遍，而重写会把文本**规范化**（`0 0 4px #000` 变成
        `0px 0px 4px 0px #000000`、声明 / 色标重排、手写的空行与注释被抹掉），看起来就像
        手写的内容被回滚。现在框里保持用户写的原样（模型 / 预览 / 右侧控件按解析结果更新），
        并继续按「手改」记着它；想要规范化文本就点「重置当前 / 重置全部」或重新生成。

        认不出任何参数时（空框 / 贴进来的是别的东西）不覆盖模型，只在标题上提示。
        """
        raw = self.wiki_edit.toPlainText()
        if not style_state.has_known_params(raw):
            self.wiki_label.setText(WIKI_LABEL_UNKNOWN)
            return
        states, tpl_states = style_state.parse_wiki_text(raw)
        self.states = states
        self.tpl_states = tpl_states
        self.gstate = style_state.copy_state(states[0])
        self._forget_code_edits()
        # 框里装的仍是用户写的文本：继续当「手改」（`_set_wiki_text` 因此不会动它）
        self._wiki_dirty = True
        self.wiki_label.setText(WIKI_LABEL_DIRTY)
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_preview()

    def _apply_code(self) -> None:
        decls = style_state.parse_code_css(self.code_edit.toPlainText())
        if not decls:
            return
        state = self.state()
        if isinstance(self.current, int):
            state["extras"] = []
        style_state.apply_decls(state, decls)
        if self.current == -1:
            self._propagate_global()
        self._code_dirty.pop(self._target_key(), None)    # 已经应用过了
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_preview()

    # ------------------------------------------------------------ 取色

    def _start_pick(self, field: ColorField) -> None:
        if self.aux.mode:                      # 吸管与辅助工具互斥
            self._set_aux_mode(None)
        if self._pick_field is not None and self._pick_field is not field:
            _set_pick_active(self._pick_field, False)
        if self._pick_field is field and self.cover_view.has_image():
            self._stop_pick()
            return
        if not self.cover_view.has_image():
            QtWidgets.QMessageBox.information(
                self, "还没有图片", "请先点「导入图片」，再在图上取色。")
            _set_pick_active(field, False)
            return
        self._pick_field = field
        _set_pick_active(field, True)
        self.cover_view.set_pick_mode(True)
        self.cover_view.setToolTip("在图上点击取色；按 Esc 退出")

    def _stop_pick(self) -> None:
        field, self._pick_field = self._pick_field, None
        _set_pick_active(field, False)
        self.cover_view.set_pick_mode(False)

    def _on_cover_picked(self, color: str) -> None:
        field = self._pick_field
        if field is None:
            return
        try:
            alpha = field.value()[1]
            field.set_value(color, 1.0 if alpha <= 0 else alpha)
        except RuntimeError:                   # 控件已经被重建掉了：忘了这次取色
            self._pick_field = None
            return
        field.changed.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            if self.aux.mode:                  # 先退辅助工具，再退吸管
                self._set_aux_mode(None)
                return
            self._stop_pick()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ 渐变 / 阴影

    def _add_layer(self) -> None:
        state = self.state()
        if len(state["bgLayers"]) >= style_state.MAX_LAYERS:
            QtWidgets.QMessageBox.information(self, "太多了", "最多 5 层渐变。")
            return
        state["bgLayers"].insert(0, style_state.default_layer())
        state["bgSet"] = True
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _add_box_shadow(self) -> None:
        state = self.state()
        if len(state["boxShadows"]) >= style_state.MAX_BOX_SHADOWS:
            return
        state["boxShadows"].append(style_state.default_box_shadow())
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _add_text_shadow(self) -> None:
        state = self.state()
        if len(state["textShadows"]) >= style_state.MAX_TEXT_SHADOWS:
            return
        state["textShadows"].append(style_state.default_text_shadow())
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _rebuild_dynamic(self) -> None:
        if self._loading:
            return
        # 重画图层 / 阴影行时，这些行里的取色器会被 `deleteLater()` 删掉 ——
        # 先把吸管收回来，免得 `_pick_field` 指着已经删除的控件（那时点保存 / 切对象
        # 都会抛 RuntimeError，用户 2026-10-03 报的「保存时界面出错」）。
        self._stop_pick()
        self._rebuild_layers()
        self._rebuild_box_shadows()
        self._rebuild_text_shadows()

    def _rebuild_layers(self) -> None:
        _clear_layout(self.layers_layout)
        state = self.state()
        for index, layer in enumerate(state["bgLayers"]):
            self.layers_layout.addWidget(self._layer_widget(index, layer))

    def _layer_widget(self, index: int, layer: Dict[str, Any]) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox(f"图层 {index + 1}", self)
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(6, 4, 4, 6)
        layout.setSpacing(4)

        head = QtWidgets.QHBoxLayout()
        kind_combo = QtWidgets.QComboBox(box)
        for key, label in (("linear", "线性"), ("radial", "径向"), ("conic", "锥形")):
            kind_combo.addItem(label, key)
        kind_combo.setCurrentIndex(max(0, kind_combo.findData(layer.get("kind"))))
        kind_combo.currentIndexChanged.connect(
            lambda _i, lay=layer, combo=kind_combo: self._update_layer(lay, kind=combo.currentData()))
        head.addWidget(kind_combo)
        repeat_check = QtWidgets.QCheckBox("重复", box)
        repeat_check.setChecked(bool(layer.get("repeat")))
        repeat_check.toggled.connect(lambda value, lay=layer: self._update_layer(lay, repeat=value))
        head.addWidget(repeat_check)
        hard_check = QtWidgets.QCheckBox("硬边", box)
        hard_check.setChecked(bool(layer.get("hard")))
        hard_check.toggled.connect(lambda value, lay=layer: self._update_layer(lay, hard=value))
        head.addWidget(hard_check)
        head.addStretch(1)
        up_button = QtWidgets.QToolButton(box)
        up_button.setText("↑")                     # 写成「上移」这一行就超宽了（右栏只有 ~435px）
        up_button.setToolTip("上移这个图层")
        up_button.clicked.connect(lambda: self._move_layer(index, -1))
        head.addWidget(up_button)
        down_button = QtWidgets.QToolButton(box)
        down_button.setText("↓")
        down_button.setToolTip("下移这个图层")
        down_button.clicked.connect(lambda: self._move_layer(index, 1))
        head.addWidget(down_button)
        remove_button = QtWidgets.QToolButton(box)
        remove_button.setText("删除")
        remove_button.setToolTip("删掉这个渐变图层")
        remove_button.clicked.connect(lambda: self._remove_layer(index))
        head.addWidget(remove_button)
        layout.addLayout(head)

        angle_row = QtWidgets.QHBoxLayout()
        angle_row.addWidget(_label("角度", box, 34))
        angle_spin = _spin(0, 360, 1, 0, "°", lambda: self._refresh_outputs())
        angle_spin.setValue(int(layer.get("angle") or 0))
        angle_spin.valueChanged.connect(lambda value, lay=layer: self._update_layer(lay, angle=value))
        angle_row.addWidget(angle_spin)
        angle_row.addWidget(_label("中心 X", box, 44))
        cx_spin = _spin(-200, 300, 1, 0, "%", lambda: self._refresh_outputs())
        cx_spin.setValue(int(layer.get("cx") or 50))
        cx_spin.valueChanged.connect(lambda value, lay=layer: self._update_layer(lay, cx=value))
        angle_row.addWidget(cx_spin)
        angle_row.addWidget(_label("Y", box, 16))
        cy_spin = _spin(-200, 300, 1, 0, "%", lambda: self._refresh_outputs())
        cy_spin.setValue(int(layer.get("cy") or 50))
        cy_spin.valueChanged.connect(lambda value, lay=layer: self._update_layer(lay, cy=value))
        angle_row.addWidget(cy_spin)
        angle_row.addStretch(1)
        layout.addLayout(angle_row)
        radial = layer.get("kind") == "radial"
        angle_spin.setEnabled(not radial)
        for widget in (cx_spin, cy_spin):
            widget.setEnabled(radial or layer.get("kind") == "conic")

        if radial:
            shape_combo = QtWidgets.QComboBox(box)
            for key, label in (("circle", "圆形"), ("ellipse", "椭圆")):
                shape_combo.addItem(label, key)
            shape_combo.setCurrentIndex(max(0, shape_combo.findData(layer.get("shape"))))
            shape_combo.currentIndexChanged.connect(
                lambda _i, lay=layer, combo=shape_combo: self._update_layer(
                    lay, shape=combo.currentData(), rebuild=True))
            size_combo = QtWidgets.QComboBox(box)
            size_combo.addItem("按百分比", "")
            for keyword in ("closest-side", "farthest-side", "closest-corner", "farthest-corner"):
                size_combo.addItem(keyword, keyword)
            size_combo.setCurrentIndex(max(0, size_combo.findData(layer.get("sizeKw") or "")))
            size_combo.currentIndexChanged.connect(
                lambda _i, lay=layer, combo=size_combo: self._update_layer(
                    lay, sizeKw=combo.currentData()))
            layout.addWidget(self._pair_row(
                box, [("形状", shape_combo, 34), ("尺寸", size_combo, 34)]))
            radius_spins: List[Tuple[str, QtWidgets.QWidget, int]] = []
            for key, label in (("sx", "横径"), ("sy", "纵径")):
                spin = _spin(1, 200, 1, 0, "%", lambda: self._refresh_outputs())
                spin.setValue(int(layer.get(key) or 50))
                spin.valueChanged.connect(
                    lambda value, lay=layer, name=key: self._update_layer(lay, **{name: value}))
                radius_spins.append((label, spin, 34))
            layout.addWidget(self._pair_row(box, radius_spins))

        stops_label = QtWidgets.QLabel("色标", box)
        layout.addWidget(stops_label)
        for stop_index, stop in enumerate(layer.get("stops") or []):
            layout.addWidget(self._stop_row(box, layer, stop_index, stop))
        add_stop = QtWidgets.QPushButton("添加色标", box)
        add_stop.clicked.connect(lambda: self._add_stop(layer))
        layout.addWidget(add_stop)
        return box

    def _pair_row(self, parent: QtWidgets.QWidget,
                  pairs: List[Tuple[str, QtWidgets.QWidget, int]]) -> QtWidgets.QWidget:
        """一行里放几组「标签 + 控件」（标签宽度可以各给各的）。

        右栏只有 ~380px 宽，塞不下就该拆行——不拆的话最后几个控件直接被裁掉看不见。
        """
        row = QtWidgets.QWidget(parent)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for text, widget, width in pairs:
            layout.addWidget(_label(text, row, width))
            layout.addWidget(widget)
        layout.addStretch(1)
        return row

    def _stop_row(self, parent: QtWidgets.QWidget, layer: Dict[str, Any],
                  index: int, stop: Dict[str, Any]) -> QtWidgets.QWidget:
        """一条色标：上面取色，下面位置 / 抗锯齿 / 删除。

        以前挤成一行（取色器 + 位置 + 抗锯齿 + 删除 ≈ 480px），右栏只有 ~380px，
        后面两个控件直接被裁掉。
        """
        row = QtWidgets.QWidget(parent)
        column = QtWidgets.QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        field = ColorField(parent=row)
        field.set_value(stop.get("color"), stop.get("alpha", 1.0))
        field.changed.connect(lambda f=field, st=stop: self._update_stop(st, *f.value()))
        field.pick_requested.connect(self._start_pick)
        column.addWidget(field)
        maximum = 360 if layer.get("kind") == "conic" else 100
        pos_spin = _spin(0, maximum, 1, 0, "%", None)
        pos_spin.setValue(int(stop.get("pos") or 0))
        pos_spin.valueChanged.connect(lambda value, st=stop: self._update_stop(st, pos=value))
        aa_check = QtWidgets.QCheckBox("抗锯齿", row)
        aa_check.setChecked(bool(stop.get("aa")))
        aa_check.toggled.connect(lambda value, st=stop: self._update_stop(st, aa=value))
        remove = QtWidgets.QToolButton(row)
        remove.setText("删除")
        remove.clicked.connect(lambda: self._remove_stop(layer, index))
        column.addWidget(self._pair_row(
            row, [("位置", pos_spin, 34), ("", aa_check, 0), ("", remove, 0)]))
        return row

    def _update_layer(self, layer: Dict[str, Any], rebuild: bool = False, **changes: Any) -> None:
        layer.update(changes)
        if rebuild:
            self._rebuild_dynamic()
        self._refresh_outputs()

    def _update_stop(self, stop: Dict[str, Any], color: Optional[str] = None,
                     alpha: Optional[float] = None, **changes: Any) -> None:
        if color is not None:
            stop["color"] = color
        if alpha is not None:
            stop["alpha"] = alpha
        stop.update(changes)
        self._refresh_outputs()

    def _add_stop(self, layer: Dict[str, Any]) -> None:
        stops = layer["stops"]
        if len(stops) >= style_state.MAX_STOPS:
            return
        last = stops[-1]
        position = 100 if not stops else min(100, (float(stops[-1].get("pos") or 100) + 100) / 2)
        stops.append({"color": last.get("color"), "alpha": last.get("alpha", 1.0),
                      "pos": position, "aa": False})
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _remove_stop(self, layer: Dict[str, Any], index: int) -> None:
        if len(layer["stops"]) <= 2:
            return
        layer["stops"].pop(index)
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _remove_layer(self, index: int) -> None:
        layers = self.state()["bgLayers"]
        if 0 <= index < len(layers):
            layers.pop(index)
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _move_layer(self, index: int, delta: int) -> None:
        layers = self.state()["bgLayers"]
        target = index + delta
        if 0 <= target < len(layers):
            layers[index], layers[target] = layers[target], layers[index]
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _rebuild_box_shadows(self) -> None:
        _clear_layout(self.box_shadows_layout)
        for index, shadow in enumerate(self.state()["boxShadows"]):
            self.box_shadows_layout.addWidget(self._shadow_row(shadow, index, box=True))

    def _rebuild_text_shadows(self) -> None:
        _clear_layout(self.text_shadows_layout)
        for index, shadow in enumerate(self.state()["textShadows"]):
            self.text_shadows_layout.addWidget(self._shadow_row(shadow, index, box=False))

    def _shadow_row(self, shadow: Dict[str, Any], index: int, box: bool) -> QtWidgets.QWidget:
        """一条阴影：偏移一行、模糊/扩散一行、颜色 + 开关/删除一行。

        四组「标签 + 数字框」加取色器原来挤在一行（1000px+），右栏只有 ~380px，
        后面一大半控件都被裁掉了。
        """
        row = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        keys = (("水平", "x"), ("垂直", "y"), ("模糊", "blur")) \
            + ((("扩散", "spread"),) if box else ())
        spins: List[Tuple[str, QtWidgets.QWidget, int]] = []
        for text, key in keys:
            spin = _spin(-40, 40, 1, 0, "", None)
            spin.setValue(int(shadow.get(key) or 0))
            spin.valueChanged.connect(
                lambda value, sh=shadow, name=key: self._update_shadow(sh, **{name: value}))
            spins.append((text, spin, 34))
        for start in range(0, len(spins), 2):
            column.addWidget(self._pair_row(row, spins[start:start + 2]))
        field = ColorField(parent=row)
        field.set_value(shadow.get("color"), shadow.get("alpha", 1.0))
        field.changed.connect(lambda f=field, sh=shadow: self._update_shadow(
            sh, color=f.value()[0], alpha=f.value()[1]))
        color_row = QtWidgets.QWidget(row)
        color_layout = QtWidgets.QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(6)
        color_layout.addWidget(field, 1)
        if box:
            inset = QtWidgets.QCheckBox("内阴影", color_row)
            inset.setChecked(bool(shadow.get("inset")))
            inset.toggled.connect(lambda value, sh=shadow: self._update_shadow(sh, inset=value))
            color_layout.addWidget(inset)
        remove = QtWidgets.QToolButton(color_row)
        remove.setText("删除")
        remove.clicked.connect(lambda: self._remove_shadow(index, box))
        color_layout.addWidget(remove)
        column.addWidget(color_row)
        return row

    def _update_shadow(self, shadow: Dict[str, Any], **changes: Any) -> None:
        shadow.update(changes)
        self._refresh_outputs()

    def _remove_shadow(self, index: int, box: bool) -> None:
        key = "boxShadows" if box else "textShadows"
        shadows = self.state()[key]
        if 0 <= index < len(shadows):
            shadows.pop(index)
        self._rebuild_dynamic()
        self._refresh_outputs()

    # ------------------------------------------------------------ 自动文字色 / 重置

    def _auto_fg(self) -> None:
        state = self.state()
        threshold = self.fg_threshold_spin.value()
        color = style_state.auto_text_color(state.get("bgSolid"), threshold)
        state["color"], state["colorAlpha"] = color, 1.0
        self.color_field.set_value(color, 1.0)
        lightness = style_state.perceived_lightness(state.get("bgSolid"))
        self.ai_tip.setText(f"底色亮度 {lightness:.1f} / 阈值 {threshold} → "
                            f"{'黑色' if color == style_state.DEFAULT_FG else '白色'}文字")
        self._refresh_outputs()

    def _reset_current(self) -> None:
        if isinstance(self.current, str):
            self.tpl_states[self.current] = self._tpl_defaults[self.current].copy()
        elif self.current == -1:
            self.gstate = style_state.copy_state(self._defaults)
            self._propagate_global()
        else:
            self.states[self.current] = style_state.copy_state(self._defaults)
            self.states[self.current]["text"] = style_state.RECT_LABELS[self.current]
        self._forget_wiki_edits()          # 重置就是把所有东西按默认重写一遍
        self._forget_code_edits()
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _reset_all(self) -> None:
        self.states = [style_state.blank_state() for _ in range(3)]
        for index in range(3):
            self.states[index]["text"] = style_state.RECT_LABELS[index]
        self.gstate = style_state.copy_state(self._defaults)
        self.tpl_states = style_state.tpl_default_states()
        self._forget_wiki_edits()
        self._forget_code_edits()
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()

    # ------------------------------------------------------------ AI

    def _load_ai_context(self) -> None:
        try:
            context = self._ai_api.get_ai_context()
        except Exception as e:                        # AI 模块不可用不影响编辑器
            context = {"enabled": False, "hidden": False, "reason": f"AI 模块不可用：{e}"}
        self._ai_context = context
        self.ai_box.setVisible(not context.get("hidden"))
        if context.get("enabled"):
            self.ai_tip.setText(f"可用 · {context.get('provider')} / {context.get('model')}")
        else:
            self.ai_tip.setText(context.get("reason") or "未配置 AI")
        self.ai_button.setEnabled(bool(context.get("enabled")))
        self._update_ai_note()

    def on_settings_changed(self) -> None:
        """「设置」页保存后重新读一遍 AI 配置（按钮可用性 / 模型名 / 默认提示词）。"""
        self._load_ai_context()

    def _reset_ai_options(self) -> None:
        """AI 面板的选项回到默认：范围 = 当前编辑对象、只改颜色 = 不勾选。

        范围只在换歌 / 重置时回来（同一次编辑里选的「全部」不该被默默改掉）。
        """
        if self.ai_scope_combo.currentData() != "cur":
            self.ai_scope_combo.setCurrentIndex(0)      # 触发 _on_ai_scope_changed：会刷新补充要求
        self.ai_color_only_check.setChecked(False)

    def _update_ai_note(self) -> None:
        """把「补充要求」预填成当前对象的默认提示词（`config.yaml` 的三栏）。

        用户手写过的内容不会被冲掉：只有框里是空的、或者还是上一次自动填进去的那份时才换。
        切 tab（Songbox / Introduction / 歌词）也走这里，所以换对象就会换默认提示词。
        """
        prompts = (self._ai_context or {}).get("prompts") or {}
        default = prompts.get(self._section_key_for_prompt(), "")
        current = self.ai_note_edit.toPlainText().strip()
        if not current or current == (self._ai_note_auto or "").strip():
            self.ai_note_edit.blockSignals(True)
            self.ai_note_edit.setPlainText(default)
            self.ai_note_edit.blockSignals(False)
        self._ai_note_auto = default

    def _section_key_for_prompt(self) -> str:
        if self.ai_scope_combo.currentData() == "all":
            return "songbox"
        if isinstance(self.current, str):
            return "lyrics" if self.section == "lyrics" else "intro"
        return "songbox"

    def _on_ai_scope_changed(self) -> None:
        self.ai_note_edit.clear()
        self._update_ai_note()

    def _on_ai_note_edited(self) -> None:
        """用户手改了「补充要求」：记下是手写的，之后切对象不再自动换成默认提示词。"""
        self._ai_note_auto = None

    def _ai_targets(self) -> List[dict]:
        color_only = self.ai_color_only_check.isChecked()
        if self.ai_scope_combo.currentData() == "all":
            return style_state.ai_all_targets(self.states, self.tpl_states, color_only)
        return style_state.ai_payload_targets(self.states, self.current, self.tpl_states, color_only)

    def _run_ai(self) -> None:
        targets = self._ai_targets()
        if not targets:
            return
        if not self.cover_view.has_image():
            QtWidgets.QMessageBox.information(self, "还没有图片", "AI 需要参考封面图，请先导入图片。")
            return
        payload = json.dumps({"scope": self.ai_scope_combo.currentData(),
                              "colorOnly": self.ai_color_only_check.isChecked(),
                              "note": self.ai_note_edit.toPlainText().strip(),
                              "targets": targets}, ensure_ascii=False)
        self._ai_requested = [str(item.get("id")) for item in targets]
        self._ai_snapshot = self._snapshot()
        self.ai_button.setEnabled(False)
        self.ai_tip.setText("正在生成…")
        self._ai_worker = FunctionWorker(self._ai_api.ai_generate, payload, parent=self)
        self._ai_worker.done.connect(self._on_ai_done)
        self._ai_worker.start()

    def _snapshot(self) -> tuple:
        return ([style_state.copy_state(state) for state in self.states],
                style_state.copy_state(self.gstate),
                {key: style_state.copy_state(value) for key, value in self.tpl_states.items()})

    def _on_ai_done(self, result: dict) -> None:
        self.ai_button.setEnabled(True)
        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "未知错误"
            self.ai_tip.setText(f"生成失败：{error}")
            return
        css_map = result.get("css") or {}
        # 只应用**本次请求过的**对象：模型多返回别的 id 时不能顺手把那一项也改了
        # （用户 2026-09-29 报：只想改 Introduction，结果多出一个 |rstyle）
        allowed = list(self._ai_requested)
        for target, css in css_map.items():
            style_state.apply_ai_css(str(target), str(css), self.states, self.tpl_states,
                                     allowed=allowed)
        self._ai_undo = self._ai_snapshot
        self.ai_undo_button.setVisible(True)
        message = f"已应用 AI 生成的样式（{result.get('model') or ''}）"
        missing = [str(item) for item in (result.get("missing") or [])]
        if missing:
            message += "；模型没返回「" + "、".join(style_state.target_label(key)
                                                  for key in missing) + \
                       "」，那几项没变（可以再生成一次）"
            logging.warning("AI 没返回这些对象：%s", "、".join(missing))
        self._apply_ai_result(message)

    def _apply_ai_result(self, message: str) -> None:
        """AI 改完模型后刷新界面：**两个文本框按新模型重写**。

        2026-09 用户报：「改完左下角的框、勾上『在 Wikitext 里输出该参数』、点 AI 生成 CSS，
        框里却没有出现 `|containerstyle`」。原因是左下角那个框一旦被手改过（`_wiki_dirty`），
        `_set_wiki_text()` 就会跳过重写 —— 那是给「切标签 / 改控件别冲掉手写内容」用的，
        结果 AI 生成的样式只进了模型与预览，框里还是旧文本；更糟的是**保存时发出去的就是
        框里的文本**（`_on_save`），AI 生成的东西会被整段丢掉。CSS 框同理（按对象记的 `_code_dirty`）。
        AI 生成跟「重置」一样属于**整个模型换掉**的动作，所以这里清掉两个手改标记再重写；
        框里那些手改、但没点过「从文本载入」的内容不再保留（提示里会说明）。
        """
        discarded = self._wiki_dirty
        self._forget_wiki_edits()
        self._forget_code_edits()
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()
        if discarded:
            message += "；左下角的参数文本已按新样式重写（之前手改、未「从文本载入」的内容不再保留）"
        self.ai_tip.setText(message)

    def _undo_ai(self) -> None:
        if not self._ai_undo:
            return
        states, gstate, tpl_states = self._ai_undo
        self.states = states
        self.gstate = gstate
        self.tpl_states = tpl_states
        self._ai_undo = None
        self.ai_undo_button.setVisible(False)
        self._apply_ai_result("已撤销这次生成")

    # ------------------------------------------------------------ 保存 / 取消

    def _on_save(self) -> None:
        self._stop_pick()
        text = self.wiki_edit.toPlainText().strip()
        if not text:
            text = self._wiki_text()
        self.saved.emit((text, self.hover_check.isChecked()))

    def _on_cancel(self) -> None:
        self._stop_pick()
        self.cancelled.emit()
