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
from utils.ui import aux_tools
from utils.ui import style_state
from utils.ui.aux_tools import AuxState
from utils.ui.style_preview import StylePreview
from utils.ui.widgets import CollapsibleBox, ColorField, CoverView, SectionTabs
from utils.ui.workers import FunctionWorker


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


class StylePanel(QtWidgets.QWidget):
    """样式编辑器页（主窗口里的一页）。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()
    settings_requested = QtCore.pyqtSignal()      # 「去设置页填密钥」→ 主窗口切到设置页

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
        self._pick_field: Optional[ColorField] = None
        self._cover_path: Optional[Path] = None
        self._ai_api = EditorApi()
        # 辅助工具（测量 / 参照物）：状态共用一份，预览台与封面各记各的坐标
        self.aux = AuxState()
        self._ai_undo: Optional[tuple] = None
        self._ai_worker = None
        self._dynamic_boxes: Dict[str, QtWidgets.QVBoxLayout] = {}
        self._build_ui()
        self._select(-1, confirm=False, section="songbox")

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)
        root.addLayout(left, 3)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        root.addLayout(right, 2)

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
        left.addWidget(self.preview)

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
        self.canvas_spin.setValue(560)
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

        left.addWidget(_label("Wikitext 参数（可直接改，改完点「从文本载入」）", self, 0))
        self.wiki_edit = QtWidgets.QPlainTextEdit(self)
        self.wiki_edit.setMinimumHeight(96)
        self.wiki_edit.setMaximumHeight(200)
        self.wiki_edit.setStyleSheet("QPlainTextEdit { font-family: Consolas, monospace; "
                                     "font-size: 12px; background: #ffffff; }")
        left.addWidget(self.wiki_edit)
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

        self.code_box = CollapsibleBox("完整 CSS（可编辑）", expanded=False, parent=self)
        self.code_edit = QtWidgets.QPlainTextEdit(self.code_box.content)
        self.code_edit.setMinimumHeight(120)
        self.code_edit.setStyleSheet("QPlainTextEdit { font-family: Consolas, monospace; "
                                     "font-size: 12px; background: #ffffff; }")
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
        left.addStretch(1)

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
        auto_layout.addWidget(_label("自动文字色", auto_row, 74))
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
        key_row = QtWidgets.QWidget(body.content)
        key_layout = QtWidgets.QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_settings_button = QtWidgets.QPushButton("去「设置」页填写密钥", key_row)
        self.ai_settings_button.setToolTip("账号、AI 密钥、服务商与模型都在侧栏底部的齿轮里改")
        self.ai_settings_button.clicked.connect(lambda: self.settings_requested.emit())
        key_layout.addWidget(self.ai_settings_button)
        key_layout.addStretch(1)
        body.add(key_row)
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
        self.ai_color_only_check.setChecked(True)
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
        self._select(-1, confirm=False, section="songbox")
        self._load_ai_context()
        # 换一首歌就清掉上一首的测量 / 参照物
        self.aux.clear_measure()
        self.aux.clear_guides()
        self._set_aux_mode(None)
        self.setFocus()

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
            self.code_edit.setPlainText(self._code_text())
            self.wiki_edit.setPlainText(self._wiki_text())
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
        self.wiki_edit.setPlainText(self._wiki_text())
        self.code_edit.setPlainText(self._code_text())
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        # 槽里抛异常在 PyQt5 里会让整个进程 abort，这里兜住并记日志
        try:
            self.preview.update_states(self.states, self.tpl_states, self.view,
                                       self.canvas_spin.value(), self.gap_spin.value(),
                                       self.current)
        except Exception as e:                       # noqa: BLE001
            logging.error("刷新预览失败：%s", e, exc_info=e)

    def _on_view_changed(self) -> None:
        self.view = self.view_combo.currentData()
        self._refresh_preview()

    def _load_from_wiki(self) -> None:
        states, tpl_states = style_state.parse_wiki_text(self.wiki_edit.toPlainText())
        self.states = states
        self.tpl_states = tpl_states
        self.gstate = style_state.copy_state(states[0])
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
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_preview()

    # ------------------------------------------------------------ 取色

    def _start_pick(self, field: ColorField) -> None:
        if self.aux.mode:                      # 吸管与辅助工具互斥
            self._set_aux_mode(None)
        if self._pick_field is not None and self._pick_field is not field:
            self._pick_field.set_pick_active(False)
        if self._pick_field is field and self.cover_view.has_image():
            self._stop_pick()
            return
        if not self.cover_view.has_image():
            QtWidgets.QMessageBox.information(
                self, "还没有图片", "请先点「导入图片」，再在图上取色。")
            field.set_pick_active(False)
            return
        self._pick_field = field
        field.set_pick_active(True)
        self.cover_view.set_pick_mode(True)
        self.cover_view.setToolTip("在图上点击取色；按 Esc 退出")

    def _stop_pick(self) -> None:
        if self._pick_field is not None:
            self._pick_field.set_pick_active(False)
        self._pick_field = None
        self.cover_view.set_pick_mode(False)

    def _on_cover_picked(self, color: str) -> None:
        field = self._pick_field
        if field is None:
            return
        alpha = field.value()[1]
        field.set_value(color, 1.0 if alpha <= 0 else alpha)
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
        up_button.setText("上移")
        up_button.clicked.connect(lambda: self._move_layer(index, -1))
        head.addWidget(up_button)
        down_button = QtWidgets.QToolButton(box)
        down_button.setText("下移")
        down_button.clicked.connect(lambda: self._move_layer(index, 1))
        head.addWidget(down_button)
        remove_button = QtWidgets.QToolButton(box)
        remove_button.setText("删除")
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
            radial_row = QtWidgets.QHBoxLayout()
            shape_combo = QtWidgets.QComboBox(box)
            for key, label in (("circle", "圆形"), ("ellipse", "椭圆")):
                shape_combo.addItem(label, key)
            shape_combo.setCurrentIndex(max(0, shape_combo.findData(layer.get("shape"))))
            shape_combo.currentIndexChanged.connect(
                lambda _i, lay=layer, combo=shape_combo: self._update_layer(
                    lay, shape=combo.currentData(), rebuild=True))
            radial_row.addWidget(shape_combo)
            size_combo = QtWidgets.QComboBox(box)
            size_combo.addItem("按百分比", "")
            for keyword in ("closest-side", "farthest-side", "closest-corner", "farthest-corner"):
                size_combo.addItem(keyword, keyword)
            size_combo.setCurrentIndex(max(0, size_combo.findData(layer.get("sizeKw") or "")))
            size_combo.currentIndexChanged.connect(
                lambda _i, lay=layer, combo=size_combo: self._update_layer(
                    lay, sizeKw=combo.currentData()))
            radial_row.addWidget(size_combo)
            for key, label in (("sx", "横径"), ("sy", "纵径")):
                radial_row.addWidget(_label(label, box, 34))
                spin = _spin(1, 200, 1, 0, "%", lambda: self._refresh_outputs())
                spin.setValue(int(layer.get(key) or 50))
                spin.valueChanged.connect(
                    lambda value, lay=layer, name=key: self._update_layer(lay, **{name: value}))
                radial_row.addWidget(spin)
            radial_row.addStretch(1)
            layout.addLayout(radial_row)

        stops_label = QtWidgets.QLabel("色标", box)
        layout.addWidget(stops_label)
        for stop_index, stop in enumerate(layer.get("stops") or []):
            layout.addWidget(self._stop_row(box, layer, stop_index, stop))
        add_stop = QtWidgets.QPushButton("添加色标", box)
        add_stop.clicked.connect(lambda: self._add_stop(layer))
        layout.addWidget(add_stop)
        return box

    def _stop_row(self, parent: QtWidgets.QWidget, layer: Dict[str, Any],
                  index: int, stop: Dict[str, Any]) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(parent)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        field = ColorField(parent=row)
        field.set_value(stop.get("color"), stop.get("alpha", 1.0))
        field.changed.connect(lambda f=field, st=stop: self._update_stop(st, *f.value()))
        field.pick_requested.connect(self._start_pick)
        layout.addWidget(field, 1)
        maximum = 360 if layer.get("kind") == "conic" else 100
        pos_spin = _spin(0, maximum, 1, 0, "%", None)
        pos_spin.setValue(int(stop.get("pos") or 0))
        pos_spin.valueChanged.connect(lambda value, st=stop: self._update_stop(st, pos=value))
        layout.addWidget(pos_spin)
        aa_check = QtWidgets.QCheckBox("抗锯齿", row)
        aa_check.setChecked(bool(stop.get("aa")))
        aa_check.toggled.connect(lambda value, st=stop: self._update_stop(st, aa=value))
        layout.addWidget(aa_check)
        remove = QtWidgets.QToolButton(row)
        remove.setText("删除")
        remove.clicked.connect(lambda: self._remove_stop(layer, index))
        layout.addWidget(remove)
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
        row = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        keys = ("x", "y", "blur", "spread") if box else ("x", "y", "blur")
        for key in keys:
            layout.addWidget(_label(key, row, 12))
            spin = _spin(-40, 40, 1, 0, "", None)
            spin.setValue(int(shadow.get(key) or 0))
            spin.valueChanged.connect(
                lambda value, sh=shadow, name=key: self._update_shadow(sh, **{name: value}))
            layout.addWidget(spin)
        field = ColorField(parent=row)
        field.set_value(shadow.get("color"), shadow.get("alpha", 1.0))
        field.changed.connect(lambda f=field, sh=shadow: self._update_shadow(
            sh, color=f.value()[0], alpha=f.value()[1]))
        layout.addWidget(field, 1)
        if box:
            inset = QtWidgets.QCheckBox("内阴影", row)
            inset.setChecked(bool(shadow.get("inset")))
            inset.toggled.connect(lambda value, sh=shadow: self._update_shadow(sh, inset=value))
            layout.addWidget(inset)
        remove = QtWidgets.QToolButton(row)
        remove.setText("删除")
        remove.clicked.connect(lambda: self._remove_shadow(index, box))
        layout.addWidget(remove)
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
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()

    def _reset_all(self) -> None:
        self.states = [style_state.blank_state() for _ in range(3)]
        for index in range(3):
            self.states[index]["text"] = style_state.RECT_LABELS[index]
        self.gstate = style_state.copy_state(self._defaults)
        self.tpl_states = style_state.tpl_default_states()
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

    def _update_ai_note(self) -> None:
        prompts = (self._ai_context or {}).get("prompts") or {}
        default = prompts.get(self._section_key_for_prompt(), "")
        if default and not self.ai_note_edit.toPlainText().strip():
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
        self._ai_note_auto = self.ai_note_edit.toPlainText()

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
        for target, css in css_map.items():
            style_state.apply_ai_css(str(target), str(css), self.states, self.tpl_states)
        self._ai_undo = self._ai_snapshot
        self.ai_undo_button.setVisible(True)
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()
        self.ai_tip.setText(f"已应用 AI 生成的样式（{result.get('model') or ''}）")

    def _undo_ai(self) -> None:
        if not self._ai_undo:
            return
        states, gstate, tpl_states = self._ai_undo
        self.states = states
        self.gstate = gstate
        self.tpl_states = tpl_states
        self._ai_undo = None
        self.ai_undo_button.setVisible(False)
        self._load_current()
        self._rebuild_dynamic()
        self._refresh_outputs()
        self.ai_tip.setText("已撤销这次生成")

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
