"""「样式」页（模板导航框用）：标题栏 / 分组栏 / 列表 三组颜色 + AI 按参考图配色。

P主模板与**歌姬模板**共用这一页（用户 2026-09-30 要求「样式页基本和生成P主模板一样」）：
除默认的 P主模板那套（参考图 = P主头像、预览走 `pt.build_template()`）外，
还可以由 `start(payload)` 传进来：

* `build`：怎么拼预览（歌姬模板传 `vocalist_template.build_main_template`）；
* `defaults`：默认六色（歌姬模板传从既有模板继承下来的那套）；
* `picture` / `loader` / `picture_label`：参考图地址、下载方式与按钮文案
  （歌姬模板用**立绘**：`prop=pageimages` 拿到的歌姬条目主图）。

对应 `{{Navbox}}` 的三个 style 参数，实测（`action=parse`）分别落在
`th.navbox-title` / `td.navbox-group` / `td.navbox-list` 上：

    |titlestyle = background:#94ceda;color:#006CAD     ← 标题栏
    |groupstyle = background:#575134;color:#FFFFFF     ← 分组栏
    |liststyle  = background:#FFF8B0;color:#3C4C54     ← 列表（空着就用 Navbox 默认的灰底）

AI 配色跟歌曲那套「样式」页共用 `utils/ai_css.py`：从参考图里量出主色，再让模型给三组
各出一份 `background / color`；这里的对象 id 就是 `title` / `group` / `list`。

参考图默认就是 **P主头像**（用户 2026-10 要求）：打开这一页就去 VocaDB 把头像下载到输出目录
（`P主头像_<P主名>.jpg`，实测地址在 `mainPicture.urlOriginal`），不用先自己找图；
想换一张点「选择参考图…」，想换回来点「P主头像」。
"""
import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import ai_css
from utils import producer_template as pt
from utils.ui import theme, widgets
from utils.ui.style_state import parse_color_or_none, parse_decl_text
from utils.ui.workers import CallbackRelay, FunctionWorker

# 对象 id → (标题, 底色 key, 字色 key)
GROUPS = (
    ("title", "标题栏", "titleBg", "titleFg"),
    ("group", "分组栏", "groupBg", "groupFg"),
    ("list", "列表", "listBg", "listFg"),
)
AI_NOTE = ("这是维基百科导航框（Navbox）的配色：标题栏最醒目（放 P主名），"
           "分组栏是「投稿的原创曲目 / 专辑」这类标签，列表是曲目列表本身。")
# 从一份 CSS 值里抓颜色候选（hex / rgb() / hsl() / 颜色名），交给 parse_color_or_none 判
COLOR_TOKEN_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|[a-zA-Z]{3,20}")


def parse_ai_styles(css_map: Dict[str, Any]) -> Dict[str, str]:
    """AI 回来的 `{title: "background:#…; color:#…;"}` → 六色字典。

    认 `background` / `background-color` / `color`；值是渐变时取第一个颜色。
    **认不出的值直接跳过**（不猜白色 —— 用户 2026-09 报过「颜色没变还多出一项」，
    就是当时把解析不出来的颜色当白色写回去了）。
    """
    styles: Dict[str, str] = {}
    for key, _label, background, color in GROUPS:
        for prop, value in parse_decl_text(str((css_map or {}).get(key) or "")):
            if prop == "color" and color not in styles:
                found = _first_color(value)
                if found:
                    styles[color] = found
            elif prop in ("background", "background-color") and background not in styles:
                found = _first_color(value)
                if found:
                    styles[background] = found
    return styles


def _first_color(value: str) -> str:
    """从一份声明值里取出第一个颜色（渐变里取第一站）。

    逐个小片段交给 `style_state.parse_color_or_none()` 认（它认 #RGB / #RRGGBB / #RRGGBBAA
    / rgb() / hsl() / 常见颜色名）；认不出的片段直接跳过。
    """
    for token in COLOR_TOKEN_RE.findall(str(value or "")):
        parsed = parse_color_or_none(token)
        if parsed:
            return str(parsed[0])
    return ""


class ColorRow(QtWidgets.QWidget):
    """一个颜色格：色块 + hex 输入 + 「默认」（空 = 模板里不写这一项）。

    这里没有用 `widgets.ColorField`：那是给歌曲条目配色用的（带透明度与吸管），
    而 Navbox 的颜色谈不上半透明，「不写这一项」也不等于「透明」，所以单独做个小控件。
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = ""
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.swatch = QtWidgets.QToolButton(self)
        self.swatch.setFixedSize(34, 24)
        self.swatch.setToolTip("点击选择颜色")
        self.swatch.clicked.connect(self._choose)
        row.addWidget(self.swatch)

        self.hex_edit = QtWidgets.QLineEdit(self)
        self.hex_edit.setMaximumWidth(88)
        self.hex_edit.setPlaceholderText("默认")
        self.hex_edit.editingFinished.connect(self._apply_hex)
        self.hex_edit.returnPressed.connect(self._apply_hex)
        row.addWidget(self.hex_edit)

        self.default_button = QtWidgets.QToolButton(self)
        self.default_button.setText("默认")
        self.default_button.setCheckable(True)
        self.default_button.setToolTip("这一项不写进模板，用 Navbox 默认色")
        self.default_button.clicked.connect(lambda: self.set_value("", notify=True))
        row.addWidget(self.default_button)
        self._refresh()

    def value(self) -> str:
        """当前颜色；空串 = 这一项不写进模板。"""
        return self._color

    def set_value(self, value: Any, notify: bool = False) -> None:
        text = str(value or "").strip()
        parsed = parse_color_or_none(text) if text else None
        self._color = str(parsed[0]) if parsed else ""
        self.hex_edit.setText(self._color)
        self._refresh()
        if notify:
            self.changed.emit()

    def _refresh(self) -> None:
        if not self._color:
            self.swatch.setStyleSheet(
                "QToolButton { border: 1px dashed #b9bfcf; background: qlineargradient("
                "x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff, stop:0.49 #ffffff,"
                " stop:0.5 #d8dbe6, stop:1 #d8dbe6); }")
            self.default_button.setChecked(True)
            return
        self.swatch.setStyleSheet(
            f"QToolButton {{ background: {self._color}; border: 1px solid #b9bfcf; }}")
        self.default_button.setChecked(False)

    def _choose(self) -> None:
        initial = QtGui.QColor(self._color or "#ffffff")
        picked = QtWidgets.QColorDialog.getColor(initial, self, "选择颜色")
        if picked.isValid():
            self.set_value(picked.name(), notify=True)

    def _apply_hex(self) -> None:
        text = self.hex_edit.text().strip()
        if not text:
            self.set_value("", notify=True)
            return
        parsed = parse_color_or_none(text)
        if not parsed:
            self.hex_edit.setToolTip("色值无效，已还原（支持 #RGB / #RRGGBB / rgb() / 颜色名）")
            self.set_value(self._color)
            return
        self.set_value(str(parsed[0]), notify=True)


class ProducerStylePanel(QtWidgets.QWidget):
    """样式页。保存发 `saved(styles)`（六色字典），取消发 `cancelled`。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work: Optional[pt.ProducerWork] = None
        self._loading = False
        self._image: Optional[Path] = None
        self._image_source = ""              # ""=没图 / "avatar"=默认参考图 / "user"=用户自己选的
        self._avatar_key = ""               # 当前参考图属于哪个地址（换 P主/歌姬 就重新下）
        self._workers: List[FunctionWorker] = []
        self._build = pt.build_template      # 预览怎么拼（歌姬模板会换掉）
        self._defaults: Dict[str, str] = dict(pt.DEFAULT_STYLES)
        self._picture = ""                   # 默认参考图地址（P主头像 / 歌姬立绘）
        self._picture_loader = None           # 下载默认参考图的函数 → {'path': …}
        self._picture_label = "P主头像"
        self._picture_note = "（VocaDB）"      # 默认参考图来源的说明（歌姬立绘留空）
        self._ai_note = AI_NOTE
        self.fields: Dict[str, ColorRow] = {}
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("模板：—", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1)
        self.image_button = QtWidgets.QPushButton("选择参考图…", self)
        self.image_button.setToolTip("AI 照这张图的配色生成三组颜色（封面、头像、随便一张图都行）；\n"
                                    "不选的话默认用 P主头像（从 VocaDB 下载）")
        self.image_button.clicked.connect(self._choose_image)
        top.addWidget(self.image_button)
        self.avatar_button = QtWidgets.QPushButton("P主头像", self)
        self.avatar_button.setToolTip("拿 P主在 VocaDB 上的头像当参考图（打开这一页时的默认选择）")
        self.avatar_button.clicked.connect(self._load_avatar)
        top.addWidget(self.avatar_button)
        self.image_label = QtWidgets.QLabel("未选参考图", self)
        self.image_label.setStyleSheet(theme.quiet_label_style())
        self.image_label.setMaximumWidth(240)
        top.addWidget(self.image_label)
        self.ai_button = QtWidgets.QPushButton("AI 配色", self)
        self.ai_button.clicked.connect(self._run_ai)
        top.addWidget(self.ai_button)
        self.reset_button = QtWidgets.QPushButton("恢复默认", self)
        self.reset_button.clicked.connect(self._reset_styles)
        top.addWidget(self.reset_button)
        root.addLayout(top)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for row, (_key, label, background, color) in enumerate(GROUPS):
            grid.addWidget(QtWidgets.QLabel(label, self), row, 0)
            for column, (key, title) in enumerate(((background, "底色"), (color, "字色")),
                                                 start=1):
                grid.addWidget(QtWidgets.QLabel(title, self), row, 2 * column - 1)
                field = ColorRow(self)
                field.setToolTip(f"{label}{title} → 写进模板的 "
                                 f"{'background' if key.endswith('Bg') else 'color'}")
                field.changed.connect(self._on_fields_changed)
                self.fields[key] = field
                grid.addWidget(field, row, 2 * column)
        grid.setColumnStretch(5, 1)
        root.addLayout(grid)

        self.hint_label = QtWidgets.QLabel(
            "「列表」两项留空就不写 `|liststyle`，用 Navbox 自带的浅灰交替底；"
            "年份小格（Navbox_subgroup）跟分组栏同一套颜色。", self)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(theme.quiet_label_style())
        root.addWidget(self.hint_label)

        self.swatch_label = QtWidgets.QLabel("", self)
        root.addWidget(self.swatch_label)

        root.addWidget(QtWidgets.QLabel("模板 wikitext（配色实时生效，只读）", self))
        self.preview = QtWidgets.QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.preview, theme.MONO_SIZE_PX, mono=True)
        root.addWidget(self.preview, 1)

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

        self._check_ai()

    # ------------------------------------------------------------ 开 / 关
    def start(self, payload: Dict[str, Any]) -> None:
        """打开这一页：`payload["work"]` 是工程对象，其余键见模块开头那段。"""
        payload = payload or {}
        self.work = payload.get("work")
        self._build = payload.get("build") or pt.build_template
        # 传了 `defaults` 就以它为准（歌姬模板传的是「空」—— 用户 2026-09-30 要求从空开始配色，
        # 这时**不要**再掺 P主那套蓝黄）；没传就还是 P主模板的默认色。
        if "defaults" in payload:
            self._defaults = dict(payload.get("defaults") or {})
        else:
            self._defaults = dict(pt.DEFAULT_STYLES)
        self._picture = payload.get("picture") or (
            pt.avatar_url(self.work.artist) if getattr(self.work, "artist", None) else "")
        self._picture_loader = payload.get("loader") or (
            (lambda: pt.download_avatar(self.work.artist))
            if getattr(self.work, "artist", None) else None)
        self._picture_label = payload.get("picture_label") or "P主头像"
        self._picture_note = payload.get("picture_note")
        if self._picture_note is None:
            self._picture_note = "（VocaDB）"
        self._ai_note = payload.get("ai_note") or AI_NOTE
        self.image_button.setToolTip(
            "AI 照这张图的配色生成三组颜色（封面、头像、立绘、随便一张图都行）；"
            f"不选的话默认用{self._picture_label}")
        self.avatar_button.setText(self._picture_label)
        self.avatar_button.setToolTip(f"拿{self._picture_label}当参考图（打开这一页时的默认选择）")
        image = payload.get("image")
        if image:
            self.set_image(image)
        self._loading = True
        try:
            name = (getattr(self.work, "template_name", "") or
                    getattr(getattr(self.work, "artist", None), "name", "") or
                    getattr(self.work, "name", "")) if self.work else ""
            self.title_label.setText(f"模板：Template:{name or '—'}")
            self._set_fields({**self._defaults, **(self.work.styles if self.work else {})})
        finally:
            self._loading = False
        self._update_preview()
        self._check_ai()
        self.set_status("调完三组颜色点「保存并继续」")
        self._maybe_load_avatar()

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录」时由主窗口调）。"""
        self.work = None
        self._image = None
        self._image_source = ""
        self._avatar_key = ""
        self._picture = ""
        self._picture_loader = None
        self._build = pt.build_template
        self._defaults = dict(pt.DEFAULT_STYLES)
        self._picture_label = "P主头像"
        self._picture_note = "（VocaDB）"
        self._ai_note = AI_NOTE
        self.image_label.setText("未选参考图")
        self.image_label.setToolTip("")
        self._loading = True
        try:
            self.title_label.setText("模板：—")
            self._set_fields(dict(self._defaults))
        finally:
            self._loading = False
        self.preview.clear()
        self.swatch_label.clear()
        self.set_status("等新一轮生成…")

    def styles(self) -> Dict[str, str]:
        """界面上现在的六色（空串表示这一项不写）。"""
        return {key: field.value() for key, field in self.fields.items()}

    def _set_fields(self, styles: Dict[str, str]) -> None:
        for key, field in self.fields.items():
            field.set_value(styles.get(key) or "")

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        widgets.set_status_text(self.status_label, text, compact=(kind == "err"))

    # ------------------------------------------------------------ 交互
    def _on_fields_changed(self) -> None:
        if not self._loading:
            self._update_preview()

    def _reset_styles(self) -> None:
        self._loading = True
        try:
            self._set_fields(dict(self._defaults))
        finally:
            self._loading = False
        self._update_preview()
        self.set_status("已恢复默认配色")

    def _update_preview(self) -> None:
        styles = self.styles()
        if self.work is not None:
            self.work.styles = styles
            self.preview.setPlainText(self._build(self.work))
        else:
            self.preview.clear()
        self.swatch_label.setText(self._swatch_html(styles))

    def _swatch_html(self, styles: Dict[str, str]) -> str:
        """一行色块，肉眼核对三组的底色与字色（取不到就按 Navbox 默认灰底显示）。"""
        chips = []
        for _key, label, background, color in GROUPS:
            bg = styles.get(background) or "#f8f9fa"
            fg = styles.get(color) or "#202122"
            note = "（默认）" if not (styles.get(background) or styles.get(color)) else ""
            chips.append(f"<span style='background:{bg};color:{fg};padding:2px 10px;"
                         f"border:1px solid #c8ccd1;'>{label}{note}：{bg} / {fg}</span>")
        return "&nbsp;".join(chips)

    def _on_save(self) -> None:
        self.saved.emit(self.styles())

    # ------------------------------------------------------------ AI 配色
    def ai_available(self) -> bool:
        """AI 能不能用（配好了密钥且没在 config.yaml 里关掉）。"""
        return bool(ai_css.context().get("enabled"))
    def _check_ai(self) -> None:
        """没配好 AI 时把按钮禁掉并说明原因（与歌曲「样式」页同一套提示）。"""
        info = ai_css.context()
        self.ai_button.setEnabled(bool(info.get("enabled")))
        if not info.get("enabled"):
            self.ai_button.setToolTip(f"AI 配色不可用：{info.get('reason')}")
        else:
            self.ai_button.setToolTip(
                f"照参考图配色（{info.get('provider')} / {info.get('model')}）；"
                "每点一次换一种用法")

    def _choose_image(self) -> None:
        start = str(self._image.parent) if self._image else ""
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择参考图", start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)")
        if path:
            self.set_image(path)

    def set_image(self, path, source: str = "") -> None:
        """设定参考图（界面点选、默认参考图与单测都走这一条）。

        `source="avatar"` 时标成「P主头像」/「歌姬立绘」（用户自己选的图就写文件名）。
        """
        self._image = Path(path)
        self._image_source = "avatar" if source == "avatar" else "user"
        if source == "avatar":
            self._avatar_key = self.artist_picture()
            self.image_label.setText(f"{self._picture_label}{self._picture_note}")
            self.image_label.setToolTip(f"默认参考图：{self._image}")
            return
        self.image_label.setText(self._image.name)
        self.image_label.setToolTip(str(self._image))

    # ------------------------------------------------------------ 参考图（默认 P主头像 / 歌姬立绘）
    def artist_picture(self) -> str:
        """当前默认参考图的地址（空串 = 没有）。"""
        return str(self._picture or "")

    def _maybe_load_avatar(self) -> None:
        """没有参考图（或参考图还是**上一个** P主/歌姬 的）时，下载默认参考图。

        用户 2026-10 要求：「选择参考图」默认就用 P主头像（歌姬模板是立绘）。所以：
        用户自己选过图就不抢；已经是当前这个的也不重复下；换了对象才重下。
        """
        url = self.artist_picture()
        self.avatar_button.setEnabled(bool(url and self._picture_loader))
        if not url:
            return
        if self._image is not None and self._image_source != "avatar":
            return                              # 用户自己选的图：不抢
        if self._image is not None and self._avatar_key == url:
            return                              # 就是当前对象的参考图
        self._load_avatar()

    def _load_avatar(self) -> None:
        """去取默认参考图（下载在后台跑，状态行报一句）。"""
        url = self.artist_picture()
        if not url or self._picture_loader is None:
            self.set_status(f"这个对象没有可用的{self._picture_label}，请自己点「选择参考图…」",
                            "warn")
            return
        if self._workers:
            return
        self._set_busy(True)
        self.set_status(f"正在下载{self._picture_label}作参考图…")
        self._avatar_key = url
        self._run_background(self._picture_loader, self._on_avatar_done)

    def _on_avatar_done(self, result: Any) -> None:
        self._set_busy(False)
        path = result.get("path") if isinstance(result, dict) else result
        if not path:
            self._avatar_key = ""
            self.set_status(f"{self._picture_label}没下载下来，可以点「选择参考图…」自己挑一张",
                            "warn")
            return
        self.set_image(path, source="avatar")
        self.set_status(f"参考图默认用 {self._picture_label}；点「AI 配色」就看它生成三组颜色")

    def ai_payload(self) -> str:
        """要发给 `ai_css.generate_css` 的 JSON（单测直接检查这一份）。"""
        image = self._image
        data = base64.b64encode(Path(image).read_bytes()).decode("ascii")
        mime = "image/png" if str(image).lower().endswith(".png") else "image/jpeg"
        return json.dumps({
            "colorOnly": True,
            "note": self._ai_note,
            "image": f"data:{mime};base64,{data}",
            "targets": [{"id": key, "label": label,
                         "props": ["background", "background-color", "color"],
                         "current": pt.style_decl(self.styles(), background, color)}
                        for key, label, background, color in GROUPS],
        }, ensure_ascii=False)

    def _run_ai(self) -> None:
        if self._image is None or not Path(self._image).is_file():
            QtWidgets.QMessageBox.information(self, "还没有参考图",
                                             "AI 需要一张参考图来取色，请先点「选择参考图…」。")
            return
        if self._workers:
            return
        try:
            payload = self.ai_payload()
        except OSError as e:
            self.set_status(f"读不了参考图：{e}", "err")
            return
        self._set_busy(True)
        self.set_status("正在生成配色…")
        self._run_background(lambda: ai_css.generate_css(payload, self._image), self._on_ai_done)

    def _on_ai_done(self, result: Any) -> None:
        self._set_busy(False)
        if not isinstance(result, dict) or not result.get("ok"):
            self.set_status(f"生成失败：{(result or {}).get('error') or '未知错误'}", "err")
            return
        css_map = result.get("css") or {}
        styles = parse_ai_styles(css_map)
        if not styles:
            self.set_status("模型没返回能认的颜色，配色没变（可以再点一次）", "warn")
            return
        self._loading = True
        try:
            self._set_fields({**self.styles(), **styles})
        finally:
            self._loading = False
        self._update_preview()
        missing = [label for key, label, _bg, _fg in GROUPS if key not in css_map]
        message = f"已应用 AI 配色（{result.get('model') or ''}）"
        if missing:
            message += "；模型没返回：" + "、".join(missing)
            logging.warning("AI 没返回这些对象：%s", "、".join(missing))
        self.set_status(message, "ok" if not missing else "warn")

    # ------------------------------------------------------------ 后台
    def _set_busy(self, busy: bool) -> None:
        for button in (self.save_button, self.cancel_button, self.ai_button,
                       self.image_button, self.avatar_button, self.reset_button):
            button.setEnabled(not busy)
        if not busy:
            self._check_ai()
            self.avatar_button.setEnabled(bool(self.artist_picture()))

    def _run_background(self, func, callback) -> None:
        """把慢调用丢到线程里；**回调在主线程执行**（见 `workers.CallbackRelay`）。"""
        worker = FunctionWorker(func, parent=self)
        self._workers.append(worker)

        def handle(result: Any) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            try:
                callback(result)
            except Exception as e:                      # noqa: BLE001 - 槽里的异常别炸进程
                logging.error("样式页回调出错：%s", e, exc_info=e)

        relay = CallbackRelay(handle, parent=self)       # 父对象持有它，不会在排队投递前被回收
        worker.done.connect(relay.done)
        worker.finished.connect(worker.deleteLater)
        worker.start()


__all__ = ["ProducerStylePanel", "ColorRow", "parse_ai_styles", "GROUPS"]
