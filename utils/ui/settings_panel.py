"""「设置」页：在界面里可视化修改 config.yaml 与 wiki_credentials.yaml。

- 侧栏底部的齿轮按钮会切到这一页（标签页一直可点，不用等流程走到）。
- 「保存」把改动写回两个文件（就地改值、保留注释），随后 `load_config()` 重新载入，**立刻生效**；
  个别项（输出目录、保存输入、语言…）会在下一步 / 下次运行时才看出来。
- 账号与 AI 密钥本来就在 `wiki_credentials.yaml` 里（打包分发时会被清空），所以样式页的
  「AI 面板」不再单独放密钥输入框，统一在这里填。
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from config.config import (config_path, credentials_path, get_ai_credentials, get_config,
                           get_wiki_credentials, load_config, save_config_values,
                           save_credentials)
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
    ("optimize_Introduction_color", "Introduction 颜色栏追加阴影 / 圆角样式"),
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


# 「应用字体」的文件选择框：Qt 只认这几种（ttc / otc 是字体集合，里面可能有好几个家族）
FONT_FILE_FILTER = ("字体文件 (*.ttf *.otf *.ttc *.otc);;所有文件 (*)")
FONT_EDIT_TIP = "点一下选字体文件（.ttf / .otf / .ttc），选完界面字体立刻换成它；" \
                "右边的「默认」可以换回去"


def _read_config_value(config: Any, path: str) -> Any:
    """按「节.键」取值；不带点就是顶格项。"""
    section, _, name = path.partition(".")
    if not name:
        return getattr(config, section, None)
    return getattr(getattr(config, section, None), name, None)


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
            self._bool_fields.append((f"{section}.{name}" if section else name, check))
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
        line.addWidget(self.ai_provider_combo)
        line.addStretch(1)
        layout.addWidget(row)
        self.ai_base_url_edit = self._add_text(layout, "ai_base_url", "接口地址")
        self.ai_model_edit = self._add_text(layout, "ai_model", "模型名")
        self.ai_key_edit = self._add_text(layout, "ai_api_key", "API 密钥", secret=True)
        self.ai_thinking_check = QtWidgets.QCheckBox("开启思考模式（仅 DeepSeek，慢但更稳）", self)
        layout.addWidget(self.ai_thinking_check)
        self._bool_fields.append(("ai_thinking", self.ai_thinking_check))

    # ------------------------------------------------------------ 读写

    def load(self) -> None:
        """从 config.yaml / wiki_credentials.yaml 读当前值填进界面。"""
        config = get_config()
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(getattr(config, "lang", "zh"))))
        self._set_font(str(getattr(config, "font_file", "") or ""),
                       str(getattr(config, "font_family", "") or ""))
        for key, widget in self._bool_fields:
            if key == "ai_thinking":
                continue
            widget.setChecked(bool(_read_config_value(config, key)))
        for key, widget in self._text_fields:
            widget.setText(str(_read_config_value(config, key) or ""))
        for key, widget in self._area_fields:
            widget.setPlainText(str(_read_config_value(config, key) or ""))
        username, password = get_wiki_credentials()
        self.username_edit.setText(username)
        self.password_edit.setText(password)
        ai = get_ai_credentials()
        self.ai_provider_combo.setCurrentIndex(
            max(0, self.ai_provider_combo.findData(ai.get("provider") or "openai")))
        self.ai_base_url_edit.setText(str(ai.get("base_url") or ""))
        self.ai_model_edit.setText(str(ai.get("model") or ""))
        self.ai_key_edit.setText(str(ai.get("api_key") or ""))
        self.ai_thinking_check.setChecked(bool(ai.get("thinking")))
        self.paths_label.setText(f"配置文件：{config_path()}\n凭据文件：{credentials_path()}")
        self.status_label.setText("改完点右上角「保存」")

    def _reload(self) -> None:
        """「放弃改动并重新载入」：界面上恢复成文件里的值；字体也要跟着退回。"""
        self.load()
        self.font_changed.emit(self._font_file, self._font_family)

    def collect(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """界面 → （配置项, 凭据项），键都是「节.键」写法。"""
        config_values: Dict[str, Any] = {"lang": self.lang_combo.currentData(),
                                        "font_family": self._font_family,
                                        "font_file": self._font_file}
        for key, widget in self._bool_fields:
            if key != "ai_thinking":
                config_values[key] = widget.isChecked()
        for key, widget in self._text_fields:
            if key not in ("username", "password", "ai_base_url", "ai_model", "ai_api_key"):
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
