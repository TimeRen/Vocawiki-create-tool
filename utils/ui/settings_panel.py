"""「设置」页：在界面里可视化修改 config.yaml 与 wiki_credentials.yaml。

- 侧栏底部的齿轮按钮会切到这一页（标签页一直可点，不用等流程走到）。
- 「保存」把改动写回两个文件（就地改值、保留注释），随后 `load_config()` 重新载入，**立刻生效**；
  个别项（输出目录、保存输入、语言…）会在下一步 / 下次运行时才看出来。
- 账号与 AI 密钥本来就在 `wiki_credentials.yaml` 里（打包分发时会被清空），所以样式页的
  「AI 面板」不再单独放密钥输入框，统一在这里填。
"""
from typing import Any, Dict, List, Tuple

from PyQt5 import QtCore, QtWidgets

from config.config import (config_path, credentials_path, get_ai_credentials, get_config,
                           get_wiki_credentials, load_config, save_config_values,
                           save_credentials)

# 各项配置：(配置键, 中文标签)
BASIC_TEXTS = (
    ("output_dir", "输出目录（留空 = 程序目录下的 output）"),
    ("save_to_file", "把输入保存到文件（留空 = 不保存，否则是文件名前缀）"),
    ("proxies", "代理（http://127.0.0.1:7890，留空 = 不用代理）"),
)
BASIC_BOOLS = (
    ("vocadb_manual", "vocadb 有重名歌曲时自己挑曲目"),
    ("vocadb_manual_url", "vocadb 搜不到时手动输入条目链接"),
)
WIKITEXT_BOOLS = (
    ("producer_template", "联网查 P主的大家族模板（{{Chinozo}}…）"),
    ("collapse_navbox", "导航框默认展开的自动补 |collapsed"),
    ("ai_lyrics", "「歌词」页显示「AI 识别并填入」"),
    ("human_original", "询问是否存在人声本家"),
    ("uploader_note", "询问是否有投稿文"),
    ("furigana_local", "歌词括号里的假名转 photrans 注音"),
    ("furigana_all", "从 Yahoo / vocadb 等站点抓振假名（暂未生效）"),
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


def _read_config_value(config: Any, path: str) -> Any:
    """按「节.键」取值；不带点就是顶格项。"""
    section, _, name = path.partition(".")
    if not name:
        return getattr(config, section, None)
    return getattr(getattr(config, section, None), name, None)


class SettingsPanel(QtWidgets.QWidget):
    """设置页（主窗口里的一页）。"""

    saved = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bool_fields: List[Tuple[str, QtWidgets.QCheckBox]] = []
        self._text_fields: List[Tuple[str, QtWidgets.QLineEdit]] = []
        self._area_fields: List[Tuple[str, QtWidgets.QPlainTextEdit]] = []
        self._build_ui()
        self.load()

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
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
        self.reload_button.clicked.connect(self.load)
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
        for key, label in BASIC_TEXTS:
            self._add_text(layout, key, label)
        self._add_bools(layout, BASIC_BOOLS, columns=2)

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

    def collect(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """界面 → （配置项, 凭据项），键都是「节.键」写法。"""
        config_values: Dict[str, Any] = {"lang": self.lang_combo.currentData()}
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
