"""侧栏头像：优先用 voca.wiki 上的真实头像，取不到就画一个占位头像。

真实头像走 MediaWiki 的 Avatar 扩展入口 `/extensions/Avatar/avatar.php?user=…`（它
302 到用户头像，没上传过就跳到站点默认头像）。注意：voca.wiki 前面挂了 Cloudflare 的
JS 挑战，`api.php` 之外的地址对非浏览器客户端一律返回 403，所以**下载失败是常态**，
这里失败时静默回退到本地画的占位头像（未登录 = 灰色人像，已登录 = 用户名首字母色块），
登录状态本身仍然是真的（靠 api.php 判断）。
"""
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import theme

AVATAR_ENTRY = "/extensions/Avatar/avatar.php"
DEFAULT_SIZE = 40
_MEMORY: Dict[str, Optional[bytes]] = {}          # username -> 图片字节 / None（取不到）

# 首字母头像的底色（都是「正常」色相，别用主题里的报错红，免得看着像出错）
MONOGRAM_COLORS = ("#3366cc", "#14866d", "#ac6600", "#6b4ba1",
                   "#0b6ba8", "#9a4d00", "#2a4b8d", "#5f6fa8")


def _origin() -> str:
    """站点地址（https://voca.wiki），取不到就返回空串。"""
    try:
        from utils import wiki_api
        return wiki_api.origin()
    except Exception as e:                          # noqa: BLE001 - 配置没读起来也不该崩
        logging.debug("取站点地址失败：%s", e)
        return ""


def avatar_url(username: str = "", size: int = 128) -> str:
    """Avatar 扩展入口地址；username 为空表示「要站点默认头像」。"""
    origin = _origin()
    if not origin:
        return ""
    url = f"{origin}{AVATAR_ENTRY}?res={int(size)}"
    if username:
        url += f"&user={quote(username)}"
    return url


def cache_path(username: str = "", size: int = 128) -> Path:
    """头像缓存文件（放临时目录，不污染工作区）。"""
    tag = username or "__default__"
    digest = hashlib.sha1(f"{tag}|{size}".encode("utf-8")).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / "vocawiki-create-tool" / "avatars"
    return directory / f"{digest}.img"


def fetch_bytes(username: str = "", size: int = 128, timeout: float = 12.0,
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


def load_bytes(username: str = "", size: int = 128, use_cache: bool = True,
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


def clear_cache() -> None:
    """清空内存缓存（退出登录 / 换账号时用）。"""
    _MEMORY.clear()


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
