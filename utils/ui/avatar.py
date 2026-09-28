"""侧栏头像：优先用 voca.wiki 上的真实头像，取不到就画一个占位头像。

头像文件在站点上是**按用户 ID** 存的：`/images/avatars/<userid>/128.png`
（`/extensions/Avatar/avatar.php?user=<名>` 只是 302 到这个地址）。

**2026-09 实测**（工具 UA / 浏览器 UA / 全套浏览器请求头 / 换出口都试过）：

* python-requests 拿不到图片字节：`/images/...`、`/extensions/...` 这些静态地址
  一律被 Cloudflare 的 JS 挑战挡住（403 `Just a moment...`），只有 `api.php` 能通，
  而 api.php 只能给图片的 URL，没有任何能取到字节的接口；
* **真 Chromium（QtWebEngine）能过**：同一个地址用 QWebEnginePage 打开就是 200 + 图片，
  连 `cf_clearance` 都不用——所以「按用户 ID 拼地址 + WebEngine 取像素」是唯一能拿到真头像的路子
  （见 `WebEngineLoader`，拿到的图会写进缓存，下次启动直接读）。

两条路都失败时回退到本地画的占位头像（未登录 = 灰底人像，已登录 = 用户名首字母色块）；
登录状态本身一直是真的（靠 api.php 判断）。
"""
import base64
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import theme

AVATAR_ENTRY = "/extensions/Avatar/avatar.php"
# Avatar 扩展实际把头像存在这里：**按用户 ID 分目录**（`/images/avatars/35/128.png`）
AVATAR_DIR = "/images/avatars"
DEFAULT_SIZE = 40                                  # 侧栏画多大（像素）
FETCH_SIZE = 128                                   # 从站点取多大的图
_MEMORY: Dict[str, Optional[bytes]] = {}          # username -> 图片字节 / None（取不到）
_USER_IDS: Dict[str, str] = {}                     # username -> 数字 ID（头像路径要用）

# 首字母头像的底色（都是「正常」色相，别用主题里的报错红，免得看着像出错）
MONOGRAM_COLORS = ("#3366cc", "#14866d", "#ac6600", "#6b4ba1",
                   "#0b6ba8", "#9a4d00", "#2a4b8d", "#5f6fa8")


def _origin() -> str:
    """站点地址（https://voca.wiki，**不带末尾斜杠**），取不到就返回空串。"""
    try:
        from utils import wiki_api
        return str(wiki_api.origin() or "").rstrip("/")
    except Exception as e:                          # noqa: BLE001 - 配置没读起来也不该崩
        logging.debug("取站点地址失败：%s", e)
        return ""


def site_name(username: str) -> str:
    """站点上真正认识的那个用户名。

    登录时填的可能是**机器人密码**的 `账户名@机器人名`（或 CentralAuth 的 `名字@wiki`），
    那种名字在站点上是查不到的（`list=users` 返回 missing、`avatar.php` 直接给默认头像），
    所以问站点之前先把后缀去掉（见 `login.wiki_username`）。
    """
    try:
        from utils import login
        return login.wiki_username(username)
    except Exception as e:                          # noqa: BLE001 - 取不到就按原样用
        logging.debug("规范化用户名失败（%s）：%s", username, e)
        return str(username or "").strip()


def avatar_url(username: str = "", size: int = 128) -> str:
    """Avatar 扩展入口地址；username 为空表示「要站点默认头像」。"""
    origin = _origin()
    if not origin:
        return ""
    url = f"{origin}{AVATAR_ENTRY}?res={int(size)}"
    name = site_name(username)
    if name:
        url += f"&user={quote(name)}"
    return url


def avatar_file_url(userid: str, size: int = FETCH_SIZE) -> str:
    """头像**文件**地址——用用户 ID 拼出来的那个（有人建议的路子，实测是对的）。

    `avatar.php?user=<名>` 会 302 到这里；自己拼的好处是不用先请求一次入口，
     但它仍然是 `/images/...` 下面的地址，**普通 HTTP 客户端取不到**（Cloudflare 403），
    真要拿字节得借 WebEngine（见 `WebEngineLoader`）。
    """
    origin = _origin()
    userid = str(userid or "").strip()
    if not origin or not userid:
        return ""
    return f"{origin}{AVATAR_DIR}/{quote(userid)}/{int(size)}.png"


def wiki_user_id(username: str, session=None) -> str:
    """查用户在 voca.wiki 上的 ID（拼头像地址要用，名字拼不出来）。取不到返回空串。"""
    name = site_name(username)
    if not name:
        return ""
    if name in _USER_IDS:
        return _USER_IDS[name]
    try:
        from utils import login
        if session is None:
            session = login.get_api_session()
        response = session.get(login.api_url(), timeout=12, params={
            "action": "query", "list": "users", "ususers": name, "format": "json"})
        users = response.json().get("query", {}).get("users") or []
        userid = str(users[0].get("userid") or "") if users else ""
    except Exception as e:                          # noqa: BLE001 - 查不到就算了，用占位头像
        logging.debug("查用户 ID 失败（%s）：%s", name, e)
        userid = ""
    if userid:
        _USER_IDS[name] = userid
    else:
        # 站点上没这个人（写错名字 / 名字带了 @机器人名 后缀）→ 拼不出头像地址，
        # 日志里留一句，免得只看到「取不到头像」却不知道是名字的问题
        logging.debug("站点上查不到用户 %s 的 ID，头像地址拼不出来", name)
    return userid


def cache_path(username: str = "", size: int = FETCH_SIZE) -> Path:
    """头像缓存文件（放临时目录，不污染工作区）。"""
    tag = username or "__default__"
    digest = hashlib.sha1(f"{tag}|{size}".encode("utf-8")).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / "vocawiki-create-tool" / "avatars"
    return directory / f"{digest}.img"


def fetch_bytes(username: str = "", size: int = FETCH_SIZE, timeout: float = 12.0,
                session=None) -> Optional[bytes]:
    """下载头像图片字节；被 Cloudflare 挡、超时、不是图片都返回 None。"""
    url = avatar_url(username, size)
    if not url:
        return None
    try:
        if session is None:
            from utils import login
            session = login.get_api_session()
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:                          # noqa: BLE001 - 网络问题一律当取不到
        logging.debug("下载头像失败（%s）：%s", username or "默认", e)
        return None
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code != 200 or not content_type.startswith("image/"):
        logging.debug("头像地址不是图片（%s %s），改用占位头像",
                      response.status_code, content_type or "无 Content-Type")
        return None
    if not response.content:
        return None
    return response.content


def load_bytes(username: str = "", size: int = FETCH_SIZE, use_cache: bool = True,
               refresh: bool = False) -> Optional[bytes]:
    """取头像字节：内存 → 磁盘缓存 → 下载（并写缓存）。取不到返回 None。"""
    key = f"{username or '__default__'}|{size}"
    if not refresh and key in _MEMORY:
        return _MEMORY[key]
    path = cache_path(username, size)
    if use_cache and not refresh and path.is_file():
        try:
            data = path.read_bytes()
            if data:
                _MEMORY[key] = data
                return data
        except OSError as e:
            logging.debug("读头像缓存失败：%s", e)
    data = fetch_bytes(username, size)
    if data:
        _MEMORY[key] = data
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            logging.debug("写头像缓存失败：%s", e)
    else:
        _MEMORY.pop(key, None)
    return data


def store_bytes(username: str, size: int, data: bytes) -> None:
    """把拿到手的头像写进内存 / 磁盘缓存（借 WebEngine 取到的图也走这里）。

    下次启动 `load_bytes()` 就能直接从缓存里读，不用再起 Chromium。
    """
    if not data:
        return
    _MEMORY[f"{username or '__default__'}|{size}"] = data
    path = cache_path(username, size)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as e:
        logging.debug("写头像缓存失败：%s", e)


def fetch_avatar(username: str, size: int = FETCH_SIZE) -> Tuple[Optional[bytes], str]:
    """后台线程里能做的部分：缓存 → 普通请求 → 查出用户 ID 拼出文件地址。

    返回 `(图片字节, 该用 WebEngine 去取的地址)`：
    * 缓存 / 普通请求拿到了 → `(bytes, "")`；
    * 没拿到（voca.wiki 的图片被 Cloudflare 挡，这是常态）→ `(None, 头像文件地址)`，
      调用方在**主线程**用 `WebEngineLoader` 去取；连地址都拼不出来 → `(None, "")`。
    """
    data = load_bytes(username, size)
    if data:
        return data, ""
    userid = wiki_user_id(username)
    if not userid:
        return None, ""
    return None, avatar_file_url(userid, size)


# 把图片画进 canvas 再把像素吐成 dataURL——WebEngine 里唯一的「取字节」办法
_GRAB_JS = """
(() => {
  const img = document.images[0];
  if (!img || !img.naturalWidth) { return ""; }
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return canvas.toDataURL("image/png");
})()
"""


def _decode_data_url(data_url: str) -> bytes:
    """把 `data:image/png;base64,…` 解回字节。"""
    try:
        _, _, payload = str(data_url or "").partition(",")
        return base64.b64decode(payload) if payload else b""
    except Exception as e:                          # noqa: BLE001 - 解不开就当没取到
        logging.debug("解析 dataURL 失败：%s", e)
        return b""


class WebEngineLoader(QtCore.QObject):
    """借 QtWebEngine（真 Chromium）把图片地址当网页打开，再把像素抠出来。

    **为什么要绕这一圈**：voca.wiki 的 `/images/...`、`/extensions/...` 全被 Cloudflare 的
    JS 挑战挡着，python-requests 一律 403（换 UA / 请求头 / 出口都没用）；
    真 Chromium 能过（2026-09 实测连 cf_clearance 都不用，直接 200），
    所以头像是「拼地址 + WebEngine」才能拿到的。**必须在主线程用**。
    """

    done = QtCore.pyqtSignal(str, object)           # (地址, 图片字节；空字节 = 没取到)

    TIMEOUT_MS = 15000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = None
        self._url = ""
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.TIMEOUT_MS)
        self._timer.timeout.connect(lambda: self._finish(b""))

    @staticmethod
    def available() -> bool:
        """这个环境能不能起 WebEngine（`--console` / 单测里会关掉，装不上也会返回假）。"""
        if os.environ.get("VOCAWIKI_NO_WEBENGINE"):
            return False
        try:
            from PyQt5 import QtWebEngineWidgets    # noqa: F401
            return True
        except Exception as e:                      # noqa: BLE001
            logging.debug("没有 QtWebEngine，头像只能画占位图：%s", e)
            return False

    def load(self, url: str) -> None:
        """开始取图；结果经 `done` 信号回来（拿不到也发，值是空字节）。"""
        from PyQt5 import QtWebEngineWidgets
        self._url = url
        self._page = QtWebEngineWidgets.QWebEnginePage(self)
        self._page.loadFinished.connect(self._on_loaded)
        self._timer.start()
        self._page.load(QtCore.QUrl(url))

    def _on_loaded(self, ok: bool) -> None:
        if self._page is None:
            return
        # loadFinished 之后图片尺寸才稳（跳转 / 图片解码都还要一拍）
        QtCore.QTimer.singleShot(300, self._extract)

    def _extract(self) -> None:
        if self._page is None:
            return
        self._page.runJavaScript(_GRAB_JS, self._on_pixels)

    def _on_pixels(self, value) -> None:
        self._finish(_decode_data_url(value) if isinstance(value, str) else b"")

    def _finish(self, data: bytes) -> None:
        self._timer.stop()
        page, self._page = self._page, None
        if page is not None:
            page.deleteLater()                      # 页面用完就扔，别留着占内存
        self.done.emit(self._url, data)


def clear_cache() -> None:
    """清空内存缓存（退出登录 / 换账号时用）。"""
    _MEMORY.clear()
    _USER_IDS.clear()


def monogram_color(username: str) -> str:
    """用户名 → 稳定的首字母底色。"""
    digest = hashlib.sha1(username.encode("utf-8")).digest()
    return MONOGRAM_COLORS[digest[0] % len(MONOGRAM_COLORS)]


def dpr() -> float:
    """当前屏幕的 devicePixelRatio（拿不到就当 1.0）。"""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def plain_pixmap(pixmap: QtGui.QPixmap) -> QtGui.QPixmap:
    """去掉 devicePixelRatio，回到「一个像素就是一个像素」的画法。"""
    image = pixmap.toImage()
    image.setDevicePixelRatio(1.0)
    return QtGui.QPixmap.fromImage(image)


def _circle_mask(pixmap: QtGui.QPixmap, device_size: int, ring: str,
                 ring_width: float = 1.6, ratio: float = 1.0) -> QtGui.QPixmap:
    """把方形图裁成圆，并描一圈边（Timeless 的细边框）。尺寸都按设备像素算。"""
    result = QtGui.QPixmap(device_size, device_size)
    result.fill(QtCore.Qt.transparent)
    result.setDevicePixelRatio(ratio)
    painter = QtGui.QPainter(result)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    rect = QtCore.QRectF(0.8, 0.8, device_size - 1.6, device_size - 1.6)
    path = QtGui.QPainterPath()
    path.addEllipse(rect)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, plain_pixmap(pixmap))
    painter.setClipping(False)
    pen = QtGui.QPen(QtGui.QColor(ring))
    pen.setWidthF(ring_width)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(rect)
    painter.end()
    return result


def placeholder_pixmap(username: str = "", size: int = DEFAULT_SIZE,
                       logged_in: bool = False) -> QtGui.QPixmap:
    """本地画的占位头像：未登录 = 灰底人像；已登录 = 用户名首字母彩底。"""
    ratio = dpr()
    raw = QtGui.QPixmap(int(size * ratio), int(size * ratio))
    raw.fill(QtCore.Qt.transparent)
    raw.setDevicePixelRatio(ratio)
    painter = QtGui.QPainter(raw)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.scale(ratio, ratio)

    if logged_in and username:
        color = monogram_color(username)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        painter.drawEllipse(QtCore.QRectF(0.8, 0.8, size - 1.6, size - 1.6))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(9.0, size * 0.42))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        painter.drawText(QtCore.QRectF(0, 0, size, size), QtCore.Qt.AlignCenter,
                         username[0].upper())
    else:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(theme.BG_SUBTLE)))
        painter.drawEllipse(QtCore.QRectF(0.8, 0.8, size - 1.6, size - 1.6))
        from utils.ui import icons
        painter.drawPixmap(QtCore.QRectF(size * 0.22, size * 0.22, size * 0.56, size * 0.56),
                           icons.pixmap("person", int(size * 0.56), theme.TEXT_MUTED),
                           QtCore.QRectF(0, 0, icons.GRID, icons.GRID))
    painter.end()
    return _circle_mask(raw, int(size * ratio),
                        theme.ACCENT if logged_in else theme.BORDER_STRONG,
                        ring_width=1.6 * ratio, ratio=ratio)


def _from_bytes(data: bytes, size: int, ring: str) -> Optional[QtGui.QPixmap]:
    """把图片字节变成圆形头像；不是图片就返回 None。"""
    image = QtGui.QImage.fromData(data)
    if image.isNull():
        return None
    ratio = dpr()
    target = int(size * ratio)
    scaled = image.scaled(target, target, QtCore.Qt.KeepAspectRatioByExpanding,
                          QtCore.Qt.SmoothTransformation)
    source = QtCore.QRect((scaled.width() - target) // 2, (scaled.height() - target) // 2,
                          target, target)
    square = scaled.copy(source)
    pixmap = QtGui.QPixmap.fromImage(square)
    return _circle_mask(pixmap, target, ring, ring_width=1.6 * ratio, ratio=ratio)


def avatar_pixmap(username: str = "", size: int = DEFAULT_SIZE, logged_in: bool = False,
                  image_bytes: Optional[bytes] = None) -> QtGui.QPixmap:
    """头像图：有真实图片就用它，否则用占位头像。"""
    ring = theme.ACCENT if logged_in else theme.BORDER_STRONG
    if image_bytes:
        pixmap = _from_bytes(image_bytes, size, ring)
        if pixmap is not None:
            return pixmap
    return placeholder_pixmap(username, size, logged_in)


__all__ = ["avatar_url", "fetch_bytes", "load_bytes", "clear_cache", "cache_path",
           "avatar_pixmap", "placeholder_pixmap", "monogram_color", "dpr",
           "plain_pixmap", "DEFAULT_SIZE", "AVATAR_ENTRY"]
