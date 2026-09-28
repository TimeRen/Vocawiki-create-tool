"""「设置」页：在界面里可视化修改 config.yaml 与 wiki_credentials.yaml。

- 侧栏底部的齿轮按钮会切到这一页（标签页一直可点，不用等流程走到）。
- 「保存」把改动写回两个文件（就地改值、保留注释），随后 `load_config()` 重新载入，**立刻生效**；
  个别项（输出目录、保存输入、语言…）会在下一步 / 下次运行时才看出来。
- 账号与 AI 密钥本来就在 `wiki_credentials.yaml` 里（打包分发时会被清空），所以样式页的
  「AI 面板」不再单独放密钥输入框，统一在这里填。
"""
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from config.config import (config_path, credentials_path, flatten_settings, get_ai_credentials,
                           get_config, get_wiki_credentials, load_config, read_settings_file,
                           save_config_values, save_credentials, split_settings)
from utils.ui import theme

# 各项配置：(配置键, 中文标签)
BASIC_TEXTS = (
    ("output_dir", "输出目录（留空 = 程序目录下的 output）"),
    ("save_to_file", "把输入保存到文件（留空 = 不保存，否则是文件名前缀）"),
    ("proxies", "代理（http://127.0.0.1:7890，留空 = 不用代理）"),
)
BASIC_BOOLS = (
    ("vocadb_manual", "vocadb 有重名歌曲时自己挑曲目"),
    ("vocadb_manual_url", "vocadb 搜不到时手动输入条目链接"),
    ("confirm_clear_history", "「清除对话记录」前弹窗确认"),
    ("font_scale_with_window", "字号随窗口大小缩放"),
)
WIKITEXT_BOOLS = (
    ("producer_template", "联网查 P主的大家族模板（{{Chinozo}}…）"),
    ("collapse_navbox", "导航框默认展开的自动补 |collapsed"),
    ("ai_lyrics", "「歌词」页显示「AI 识别并填入」"),
    ("human_original", "询问是否存在人声本家"),
    ("other_versions", "询问是否加入同一首歌的其他版本（Tab 切换）"),
    ("uploader_note", "询问是否有投稿文"),
    ("furigana_all", "AI 生成振假名（给日语歌词里没写读音的汉字补 {{photrans}}）"),
)
COLOR_BOOLS = (
    ("color_editor", "启用可视化样式编辑器（「样式」页）"),
    ("ai_css", "样式页显示「AI 参考封面生成 CSS」"),
)
IMAGE_BOOLS = (
    ("download_cover", "下载封面（自动挑分辨率最大的一张）"),
    ("crop", "自动裁剪封面黑边"),
)
WIKI_BOOLS = (
    ("submit_window", "生成后切到「提交」页（预览 / 编辑 / 提交 / 上传封面）"),
    ("create_redirect", "提交时创建「日文原名 → 中文条目」重定向"),
    ("disambiguate", "同名条目（消歧义）处理"),
)
AI_PROMPT_FIELDS = (
    ("ai_prompt_songbox", "Songbox"),
    ("ai_prompt_intro", "Introduction"),
    ("ai_prompt_lyrics", "歌词"),
)
LANGUAGES = (("zh", "中文"), ("en", "English"))
AI_PROVIDERS = (("openai", "openai（OpenAI 兼容接口）"), ("anthropic", "anthropic（消息接口）"))

# 这几个文本框属于 wiki_credentials.yaml（`collect()` / `_apply()` 都要跳过它们，别写进 config.yaml）
CREDENTIAL_TEXT_KEYS = ("username", "password", "ai_base_url", "ai_model", "ai_api_key")


# 「应用字体」的文件选择框：Qt 只认这几种（ttc / otc 是字体集合，里面可能有好几个家族）
FONT_FILE_FILTER = ("字体文件 (*.ttf *.otf *.ttc *.otc);;所有文件 (*)")
FONT_EDIT_TIP = "点一下选字体文件（.ttf / .otf / .ttc），选完界面字体立刻换成它；" \
                "右边的「默认」可以换回去"

# 「导入配置文件」的文件选择框：config.yaml 与 wiki_credentials.yaml 都走它
IMPORT_FILE_FILTER = "设置文件 (*.yaml *.yml);;所有文件 (*)"


class FontPathEdit(QtWidgets.QLineEdit):
    """只读输入栏当按钮用：点一下就发 `browse_requested`。

    `QLineEdit` 设成只读后自己不吃鼠标点击，所以直接重写 mouseReleaseEvent；
    回车 / 空格也认，键盘一样能用（用户 2026-09 要求「点输入栏弹出选字体文件」）。
    """

    browse_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:      # noqa: N802 - Qt 约定
        super().mouseReleaseEvent(event)
        if event.button() == QtCore.Qt.LeftButton:
            self.browse_requested.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:            # noqa: N802 - Qt 约定
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space):
            self.browse_requested.emit()
            return
        super().keyPressEvent(event)


class SettingsPanel(QtWidgets.QWidget):
    """设置页（主窗口里的一页）。"""

    saved = QtCore.pyqtSignal()
    # 刚选好字体文件（还没「保存」）：主窗口收到就先把字体换上去
    font_changed = QtCore.pyqtSignal(str, str)      # (字体文件路径, 家族名)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bool_fields: List[Tuple[str, QtWidgets.QCheckBox]] = []
        self._text_fields: List[Tuple[str, QtWidgets.QLineEdit]] = []
        self._area_fields: List[Tuple[str, QtWidgets.QPlainTextEdit]] = []
        self._labels: Dict[str, str] = {}          # 配置键 → 界面上的中文名（导入时用来报「改了哪几项」）
        self._font_file = ""                       # 当前选中的字体文件（空 = 用默认字体）
        self._font_family = ""                     # 该文件里的家族名（空 = 默认）
        self._build_ui()
        self.load()

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("设置", self)
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        title.setFont(font)
        head.addWidget(title)
        head.addStretch(1)
        self.import_button = QtWidgets.QPushButton("导入配置文件", self)
        self.import_button.setToolTip("选一个 config.yaml 或 wiki_credentials.yaml，"
                                      "把里面的值填进这一页（还要点「保存」才写回）")
        self.import_button.clicked.connect(self._import_file)
        head.addWidget(self.import_button)
        self.reload_button = QtWidgets.QPushButton("放弃改动并重新载入", self)
        self.reload_button.clicked.connect(self._reload)
        head.addWidget(self.reload_button)
        self.save_button = QtWidgets.QPushButton("保存", self)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        head.addWidget(self.save_button)
        root.addLayout(head)

        self.paths_label = QtWidgets.QLabel("", self)
        self.paths_label.setWordWrap(True)
        self.paths_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.paths_label.setStyleSheet("QLabel { color: #54595d; }")
        root.addWidget(self.paths_label)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        holder = QtWidgets.QWidget(scroll)
        self.form_layout = QtWidgets.QVBoxLayout(holder)
        self.form_layout.setContentsMargins(0, 0, 8, 0)
        self.form_layout.setSpacing(8)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        self._build_basic_box()
        self._build_wikitext_box()
        self._build_color_box()
        self._build_image_box()
        self._build_wiki_box()
        self._build_credentials_box()
        self.form_layout.addStretch(1)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("改完点右上角「保存」", self)
        self.status_label.setStyleSheet("QLabel { color: #54595d; }")
        footer.addWidget(self.status_label)
        footer.addStretch(1)
        root.addLayout(footer)

    def _group(self, title: str) -> Tuple[QtWidgets.QGroupBox, QtWidgets.QVBoxLayout]:
        box = QtWidgets.QGroupBox(title, self)
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(5)
        self.form_layout.addWidget(box)
        return box, layout

    def _add_text(self, layout: QtWidgets.QVBoxLayout, key: str, label: str,
                  secret: bool = False) -> QtWidgets.QLineEdit:
        row = QtWidgets.QWidget(layout.parentWidget())
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(QtWidgets.QLabel(label, row))
        edit = QtWidgets.QLineEdit(row)
        if secret:
            edit.setEchoMode(QtWidgets.QLineEdit.Password)
            toggle = QtWidgets.QToolButton(row)
            toggle.setText("显示")
            toggle.setCheckable(True)
            toggle.toggled.connect(lambda checked, e=edit, t=toggle: self._switch_echo(e, t, checked))
            line.addWidget(edit, 1)
            line.addWidget(toggle)
        else:
            line.addWidget(edit, 1)
        layout.addWidget(row)
        self._text_fields.append((key, edit))
        self._labels.setdefault(key, label)
        return edit

    def _add_bools(self, layout: QtWidgets.QVBoxLayout, fields: Tuple[Tuple[str, str], ...],
                   columns: int = 1, section: str = "") -> None:
        """加一组复选框；section 不为空时配置键带节名前缀（wikitext.xxx）。"""
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        rows = max(1, (len(fields) + columns - 1) // columns)
        for index, (name, label) in enumerate(fields):
            check = QtWidgets.QCheckBox(label, layout.parentWidget())
            grid.addWidget(check, index % rows, index // rows)
            key = f"{section}.{name}" if section else name
            self._bool_fields.append((key, check))
            self._labels.setdefault(key, label)
        layout.addLayout(grid)

    @staticmethod
    def _switch_echo(edit: QtWidgets.QLineEdit, toggle: QtWidgets.QToolButton,
                     shown: bool) -> None:
        edit.setEchoMode(QtWidgets.QLineEdit.Normal if shown else QtWidgets.QLineEdit.Password)
        toggle.setText("隐藏" if shown else "显示")

    def _build_basic_box(self) -> None:
        _box, layout = self._group("基本")
        row = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(QtWidgets.QLabel("界面语言", row))
        self.lang_combo = QtWidgets.QComboBox(row)
        for code, label in LANGUAGES:
            self.lang_combo.addItem(label, code)
        self._labels.setdefault("lang", "界面语言")
        line.addWidget(self.lang_combo)
        line.addStretch(1)
        layout.addWidget(row)
        layout.addWidget(self._build_font_row())
        for key, label in BASIC_TEXTS:
            self._add_text(layout, key, label)
        self._add_bools(layout, BASIC_BOOLS, columns=2)

    def _build_font_row(self) -> QtWidgets.QWidget:
        """「应用字体」一行：点输入栏弹出文件选择框，选一个字体文件（用户 2026-09 要求）。

        选完马上把文件注册进 Qt 并换字体（不等「保存」）；「保存」只是把路径写进
        config.yaml（`font_file`），下次启动照它重新加载——字体没装在系统里也能用。
        """
        row = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(QtWidgets.QLabel("应用字体", row))
        self.font_edit = FontPathEdit(row)
        self.font_edit.setPlaceholderText(f"点这里选字体文件（默认 {theme.DEFAULT_FONT_FAMILY}）")
        self.font_edit.setToolTip(FONT_EDIT_TIP)
        self.font_edit.browse_requested.connect(self._choose_font_file)
        self._labels.setdefault("font_file", "应用字体")
        self._labels.setdefault("font_family", "应用字体")
        line.addWidget(self.font_edit, 1)
        reset = QtWidgets.QPushButton("默认", row)
        reset.setToolTip("换回默认字体（删掉 config.yaml 里的 font_file）")
        reset.clicked.connect(self._reset_font)
        line.addWidget(reset)
        return row

    # —— 「应用字体」相关动作 ——

    def _choose_font_file(self) -> None:
        """弹文件框选字体文件；选完立刻生效（保存后才写进 config.yaml）。"""
        start_dir = ""
        if self._font_file:
            start_dir = str(Path(self._font_file).parent)
        else:
            fonts_dir = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
            start_dir = str(fonts_dir) if fonts_dir.exists() else str(Path.home())
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择字体文件", start_dir, FONT_FILE_FILTER)
        if not path:
            return
        family = theme.load_font_file(path)
        if not family:
            self._show_status(f"读不出这个字体文件：{Path(path).name}", ok=False)
            return
        self._set_font(path, family)
        theme.apply_font(family, path)
        self.font_changed.emit(path, family)        # 主窗口收到就重套样式表
        self._show_status(f"已换成「{family}」（点右上角「保存」写进 config.yaml）")

    def _reset_font(self) -> None:
        """换回默认字体。"""
        if not self._font_file and not self._font_family:
            return
        self._set_font("", "")
        theme.apply_font("", "")
        self.font_changed.emit("", "")
        self._show_status("已换回默认字体（点右上角「保存」写进 config.yaml）")

    def _set_font(self, path: str, family: str) -> None:
        """记下当前选的字体文件 / 家族名，并把输入栏上的字换掉。"""
        self._font_file = str(path or "")
        self._font_family = str(family or "")
        if self._font_file:
            self.font_edit.setText(f"{self._font_family}（{Path(self._font_file).name}）")
            self.font_edit.setToolTip(f"{self._font_file}\n"
                                      f"字体：{self._font_family}；点一下可以重新选")
        else:
            self.font_edit.clear()
            self.font_edit.setToolTip(FONT_EDIT_TIP)

    def _show_status(self, text: str, ok: bool = True) -> None:
        self.status_label.setStyleSheet(
            f"QLabel {{ color: {theme.SUCCESS if ok else theme.DANGER}; }}")
        self.status_label.setText(text)

    def _build_wikitext_box(self) -> None:
        _box, layout = self._group("生成内容（wikitext）")
        self._add_bools(layout, WIKITEXT_BOOLS, columns=2, section="wikitext")

    def _build_color_box(self) -> None:
        _box, layout = self._group("样式编辑器（color）")
        self._add_bools(layout, COLOR_BOOLS, columns=1, section="color")
        layout.addWidget(QtWidgets.QLabel("AI 生成 CSS 时预填的提示词（留空则不预填）", self))
        for key, label in AI_PROMPT_FIELDS:
            layout.addWidget(QtWidgets.QLabel(f"  {label}", self))
            edit = QtWidgets.QPlainTextEdit(self)
            edit.setMinimumHeight(52)
            edit.setMaximumHeight(80)
            layout.addWidget(edit)
            self._area_fields.append((f"color.{key}", edit))
            self._labels.setdefault(f"color.{key}", label)

    def _build_image_box(self) -> None:
        _box, layout = self._group("封面图片（image）")
        self._add_bools(layout, IMAGE_BOOLS, columns=2, section="image")

    def _build_wiki_box(self) -> None:
        _box, layout = self._group("Vocawiki（wiki）")
        self._add_text(layout, "wiki.api_url", "API 地址")
        self._add_bools(layout, WIKI_BOOLS, columns=1, section="wiki")

    def _build_credentials_box(self) -> None:
        _box, layout = self._group("账号与密钥（wiki_credentials.yaml）")
        note = QtWidgets.QLabel(
            "建议用机器人密码（Special:BotPasswords，username 写成「账户名@机器人名」）。"
            "打包分发时会自动清空这三个字段，不用担心泄露。", self)
        note.setWordWrap(True)
        note.setStyleSheet("QLabel { color: #54595d; }")
        layout.addWidget(note)
        self.username_edit = self._add_text(layout, "username", "用户名")
        self.password_edit = self._add_text(layout, "password", "密码", secret=True)

        row = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(QtWidgets.QLabel("接口类型", row))
        self.ai_provider_combo = QtWidgets.QComboBox(row)
        for code, label in AI_PROVIDERS:
            self.ai_provider_combo.addItem(label, code)
        self._labels.setdefault("ai_provider", "接口类型")
        line.addWidget(self.ai_provider_combo)
        line.addStretch(1)
        layout.addWidget(row)
        self.ai_base_url_edit = self._add_text(layout, "ai_base_url", "接口地址")
        self.ai_model_edit = self._add_text(layout, "ai_model", "模型名")
        self.ai_key_edit = self._add_text(layout, "ai_api_key", "API 密钥", secret=True)
        self.ai_thinking_check = QtWidgets.QCheckBox("开启思考模式（仅 DeepSeek，慢但更稳）", self)
        layout.addWidget(self.ai_thinking_check)
        self._bool_fields.append(("ai_thinking", self.ai_thinking_check))
        self._labels.setdefault("ai_thinking", "开启思考模式")

    # ------------------------------------------------------------ 读写

    def load(self) -> None:
        """从 config.yaml / wiki_credentials.yaml 读当前值填进界面。"""
        username, password = get_wiki_credentials()
        ai = get_ai_credentials()
        self._apply(flatten_settings(get_config()), {
            "username": username,
            "password": password,
            "ai_provider": ai.get("provider") or "openai",
            "ai_base_url": ai.get("base_url") or "",
            "ai_model": ai.get("model") or "",
            "ai_api_key": ai.get("api_key") or "",
            "ai_thinking": bool(ai.get("thinking")),
        })
        self.paths_label.setText(f"配置文件：{config_path()}\n凭据文件：{credentials_path()}")
        self.status_label.setText("改完点右上角「保存」")

    def _apply(self, config_values: Mapping[str, Any],
               credential_values: Mapping[str, Any]) -> None:
        """把两份**扁平**设置填进控件（键是「节.键」，如 `wikitext.producer_template`）。

        只认传进来的键：没传的键保持控件现状 —— 这样「导入配置文件」可以拿几行就改几项，
        不会把文件里没写的设置一并清空。
        """
        lang = config_values.get("lang", self.lang_combo.currentData())
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(lang)))
        self._set_font(str(config_values.get("font_file", self._font_file) or ""),
                       str(config_values.get("font_family", self._font_family) or ""))
        for key, widget in self._bool_fields:
            if key == "ai_thinking":
                continue
            widget.setChecked(bool(config_values.get(key, widget.isChecked())))
        for key, widget in self._text_fields:
            if key in CREDENTIAL_TEXT_KEYS:
                continue
            widget.setText(str(config_values.get(key, widget.text()) or ""))
        for key, widget in self._area_fields:
            widget.setPlainText(str(config_values.get(key, widget.toPlainText()) or ""))
        self.username_edit.setText(str(credential_values.get("username",
                                                               self.username_edit.text()) or ""))
        self.password_edit.setText(str(credential_values.get("password",
                                                               self.password_edit.text()) or ""))
        provider = credential_values.get("ai_provider", self.ai_provider_combo.currentData())
        self.ai_provider_combo.setCurrentIndex(
            max(0, self.ai_provider_combo.findData(provider or "openai")))
        self.ai_base_url_edit.setText(str(credential_values.get("ai_base_url",
                                                                    self.ai_base_url_edit.text()) or ""))
        self.ai_model_edit.setText(str(credential_values.get("ai_model",
                                                               self.ai_model_edit.text()) or ""))
        self.ai_key_edit.setText(str(credential_values.get("ai_api_key",
                                                             self.ai_key_edit.text()) or ""))
        self.ai_thinking_check.setChecked(bool(credential_values.get(
            "ai_thinking", self.ai_thinking_check.isChecked())))

    def _reload(self) -> None:
        """「放弃改动并重新载入」：界面上恢复成文件里的值；字体也要跟着退回。"""
        self.load()
        self.font_changed.emit(self._font_file, self._font_family)

    # —— 「导入配置文件」 ——

    def _import_start_dir(self) -> str:
        """文件框的起始目录：程序目录（config.yaml 就在那儿）；没有就用主目录。"""
        folder = Path(config_path()).parent
        return str(folder) if folder.exists() else str(Path.home())

    def _known_keys(self) -> set:
        """这一页能显示 / 能填的键（导入时用来判断一份文件里有什么是「认得的」）。"""
        keys = {key for key, _widget in self._text_fields}
        keys.update(key for key, _widget in self._area_fields)
        keys.update(key for key, _widget in self._bool_fields)
        keys.update({"lang", "ai_provider", "font_file", "font_family"})
        return keys

    def _snapshot(self) -> Dict[str, Any]:
        """界面上的当前值（扁平）：导入前后各取一份，用来比出「到底改了哪几项」。"""
        values: Dict[str, Any] = {"lang": self.lang_combo.currentData(),
                                  "ai_provider": self.ai_provider_combo.currentData(),
                                  "font_file": self._font_file,
                                  "font_family": self._font_family}
        for key, widget in self._bool_fields:
            values[key] = widget.isChecked()
        for key, widget in self._text_fields:
            values[key] = widget.text()
        for key, widget in self._area_fields:
            values[key] = widget.toPlainText()
        return values

    def _label_list(self, keys: List[str]) -> str:
        """把若干配置键写成「中文名、中文名…」（太多就只列前几个 + 共几项）。"""
        names = list(dict.fromkeys(self._labels.get(key, key) for key in keys))
        if len(names) > 4:
            return f"{'、'.join(names[:4])} 等 {len(names)} 项"
        return "、".join(names)

    def _import_file(self) -> None:
        """选 config.yaml / wiki_credentials.yaml，把里面的值填进这一页。

        **只改界面，不写盘**（检查无误后点右上角「保存」才落到两个文件里）；
        文件里认得的键才覆盖，没写的项保持界面现状 —— 导入一份残缺的配置也不会把
        其它设置清空。可以一次选多个文件（两份一起导入）。

        值按**键**归位（`split_settings`），不看文件叫什么名字：凭据文件里顺手写了
        `proxies`、或者 config.yaml 里写了 `username`，都能各回各家。2026-09 用户报
        「导入 wiki_credentials.yaml 没反应」：混合文件被判成 config，凭据那几项被整份丢掉。
        状态栏会把这次到底动了哪几项写出来（值本来就一样就说一声），不然看着像没生效。
        """
        try:
            paths, _selected = QtWidgets.QFileDialog.getOpenFileNames(
                self, "导入配置文件（config.yaml / wiki_credentials.yaml）",
                self._import_start_dir(), IMPORT_FILE_FILTER)
        except Exception as error:                          # noqa: BLE001 - 别让文件框把流程带走
            logging.exception("打开文件选择框失败：%s", error)
            self._show_status(f"打开文件选择框失败：{error}", ok=False)
            return
        if not paths:
            return
        config_values, credential_values = self.collect()      # 先拿界面上的当前值
        known = self._known_keys()
        imported: List[str] = []
        touched: List[str] = []
        problems: List[str] = []
        for path in paths:
            name = Path(path).name
            try:
                kind, values, error = read_settings_file(path)
            except Exception as error:                      # noqa: BLE001 - 文件千奇百怪
                logging.exception("导入 %s 出错：%s", path, error)
                problems.append(f"{name}（{type(error).__name__}: {error}）")
                continue
            if not kind:
                logging.warning("导入 %s 失败：%s", path, error)
                problems.append(f"{name}（{error}）")
                continue
            file_config, file_credential = split_settings(values)
            recognized = [key for key in list(file_config) + list(file_credential) if key in known]
            if not recognized:
                problems.append(f"{name}（里面没有本页认得的设置项）")
                continue
            config_values.update(file_config)
            credential_values.update(file_credential)
            imported.append(name)
            touched.extend(recognized)
        if imported:
            before = self._snapshot()
            self._apply(config_values, credential_values)
            # 字体跟「放弃改动并重新载入」一样当场就套上（还没保存）
            self.font_changed.emit(self._font_file, self._font_family)
            after = self._snapshot()
            changed = [key for key in touched if before.get(key) != after.get(key)]
        else:
            changed = []
        failed = "；".join(problems)
        if not imported:
            self._show_status(f"导入失败：{failed}", ok=False)
            return
        summary = self._label_list(changed) if changed else "里面的值与当前一致，没有要改的地方"
        message = f"已导入 {'、'.join(imported)}：{summary}"
        if changed:
            message += "（检查后点右上角「保存」写回文件）"
        if problems:
            message += f"；导入失败：{failed}"
        self._show_status(message, ok=not problems)

    def collect(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """界面 → （配置项, 凭据项），键都是「节.键」写法。"""
        config_values: Dict[str, Any] = {"lang": self.lang_combo.currentData(),
                                        "font_family": self._font_family,
                                        "font_file": self._font_file}
        for key, widget in self._bool_fields:
            if key != "ai_thinking":
                config_values[key] = widget.isChecked()
        for key, widget in self._text_fields:
            if key not in CREDENTIAL_TEXT_KEYS:
                config_values[key] = widget.text().strip()
        for key, widget in self._area_fields:
            config_values[key] = widget.toPlainText().strip()
        credential_values: Dict[str, Any] = {
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text().strip(),
            "ai_provider": self.ai_provider_combo.currentData(),
            "ai_base_url": self.ai_base_url_edit.text().strip(),
            "ai_model": self.ai_model_edit.text().strip(),
            "ai_api_key": self.ai_key_edit.text().strip(),
            "ai_thinking": self.ai_thinking_check.isChecked(),
        }
        return config_values, credential_values

    def save(self) -> bool:
        """写回两个文件并重新载入配置（改动立刻生效）。"""
        config_values, credential_values = self.collect()
        config_ok = save_config_values(config_values)
        credentials_ok = save_credentials(credential_values)
        if not (config_ok and credentials_ok):
            failed = "config.yaml" if not config_ok else "wiki_credentials.yaml"
            self.status_label.setText(f"保存失败：写不了 {failed}（详见「日志」页）")
            self.status_label.setStyleSheet("QLabel { color: #b32424; }")
            return False
        load_config(config_path())             # 重新载入：后面用到 get_config() 的地方都拿到新值
        self.load()
        self.status_label.setStyleSheet("QLabel { color: #14866d; }")
        self.status_label.setText("已保存到 config.yaml 与 wiki_credentials.yaml（已重新载入）")
        self.saved.emit()
        return True
