"""界面图标：用 QPainter 画出来的单色图标。

除「歌」字（`song`）用系统字体写字外，其余都是在 20×20 的网格上现画的几何图形，
不引第三方图标库、也不依赖字体里有没有某个符号；颜色随参数变（默认取主题的次要
文字色），侧栏选中 / 未选中都能对上。
"""
import math
from typing import Optional

from PyQt5 import QtCore, QtGui

from utils.ui import theme

GRID = 20.0                                    # 所有图标都按 20×20 设计
SONG_CHAR = "歌"                               # 「生成歌曲条目」功能用的字图标


def _painter(pixmap: QtGui.QPixmap, color: str, width: float) -> QtGui.QPainter:
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.scale(pixmap.width() / GRID, pixmap.height() / GRID)
    return painter


def _blank(size: int, ratio: float) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(QtCore.Qt.transparent)
    return pixmap


def _draw_char(painter: QtGui.QPainter, text: str, color: str,
               pixel_size: float = 15.0) -> None:
    """在 20×20 网格里居中画一个汉字（用系统字体，缺字时会自动回退到能显示中文的字体）。"""
    font = QtGui.QFont("Microsoft YaHei UI")
    font.setStyleHint(QtGui.QFont.SansSerif)
    font.setPixelSize(int(round(pixel_size)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QtGui.QPen(QtGui.QColor(color)))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawText(QtCore.QRectF(0, 0, GRID, GRID), QtCore.Qt.AlignCenter, text)


def pixmap(name: str, size: int = 20, color: Optional[str] = None) -> QtGui.QPixmap:
    """画一个图标；name 见下面各分支，未知名字给一个空心圆。"""
    color = color or theme.TEXT_QUIET
    canvas = _blank(size, 1.0)
    painter = _painter(canvas, color, 1.8)
    brush = QtGui.QBrush(QtGui.QColor(color))

    if name == "song":                          # 生成歌曲条目：一个「歌」字
        _draw_char(painter, SONG_CHAR, color)

    elif name == "list":                       # 条目列表（旧图标，留着备用）
        for index, (left, right) in enumerate(((3.5, 16.5), (3.5, 16.5), (3.5, 12.0))):
            y = 5.5 + index * 4.5
            painter.drawLine(QtCore.QPointF(left, y), QtCore.QPointF(right, y))

    elif name == "gear":                        # 设置：八齿 + 中心孔
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.OddEvenFill)
        teeth, outer, inner = 8, 8.6, 6.1
        for index in range(teeth * 2):
            angle = math.pi * index / teeth - math.pi / 2
            radius = outer if index % 2 == 0 else inner
            point = QtCore.QPointF(GRID / 2 + radius * math.cos(angle),
                                   GRID / 2 + radius * math.sin(angle))
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        path.closeSubpath()
        path.addEllipse(QtCore.QPointF(GRID / 2, GRID / 2), 2.7, 2.7)
        painter.setBrush(brush)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawPath(path)

    elif name == "person":                      # 头像占位：头 + 肩
        painter.setBrush(brush)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(10, 7.6), 3.5, 3.5)
        painter.drawEllipse(QtCore.QRectF(3.4, 12.9, 13.2, 9.4))

    elif name == "logout":                      # 退出登录：方框 + 外出的箭头
        painter.drawRoundedRect(QtCore.QRectF(3.2, 3.2, 7.6, 13.6), 1.4, 1.4)
        painter.drawLine(QtCore.QPointF(10.4, 10), QtCore.QPointF(16.6, 10))
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(13.8, 7.2),
                                              QtCore.QPointF(16.8, 10),
                                              QtCore.QPointF(13.8, 12.8)]))

    elif name == "refresh":                     # 重新登录：环形箭头
        painter.drawArc(QtCore.QRectF(4.2, 4.2, 11.6, 11.6), 60 * 16, 250 * 16)
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(12.2, 3.4),
                                              QtCore.QPointF(14.6, 5.6),
                                              QtCore.QPointF(12.0, 7.6)]))

    elif name == "tune":                        # 账号与密钥：滑块
        painter.drawLine(QtCore.QPointF(3.5, 6.5), QtCore.QPointF(16.5, 6.5))
        painter.drawLine(QtCore.QPointF(3.5, 13.5), QtCore.QPointF(16.5, 13.5))
        painter.setBrush(brush)
        for x, y in ((12.5, 6.5), (7.5, 13.5)):
            painter.drawEllipse(QtCore.QPointF(x, y), 2.1, 2.1)

    else:
        painter.drawEllipse(QtCore.QRectF(4, 4, 12, 12))

    painter.end()
    return canvas


def icon(name: str, size: int = 20, color: Optional[str] = None) -> QtGui.QIcon:
    return QtGui.QIcon(pixmap(name, size, color))


def brand_pixmap(size: int = 30, color: Optional[str] = None) -> QtGui.QPixmap:
    """没有程序图标时用的品牌方块（Timeless 左上角那种小色块）。"""
    color = color or theme.ACCENT
    canvas = _blank(size, 1.0)
    painter = _painter(canvas, color, 1.0)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
    painter.drawRoundedRect(QtCore.QRectF(1, 1, GRID - 2, GRID - 2), 3.2, 3.2)
    painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
    font = painter.font()
    font.setPointSizeF(11.0)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QtCore.QRectF(1, 0.6, GRID - 2, GRID - 2),
                     QtCore.Qt.AlignCenter, "V")
    painter.end()
    return canvas


def has_fonts() -> bool:
    """环境里有没有可用字体（offscreen 平台常常一个都没有，则文字图标画不出来）。"""
    probe = _blank(20, 1.0)
    painter = _painter(probe, "#000000", 1.0)
    _draw_char(painter, SONG_CHAR, "#000000")
    painter.end()
    image = probe.toImage()
    return any(image.pixelColor(x, y).alpha() > 0
               for x in range(probe.width()) for y in range(probe.height()))


__all__ = ["pixmap", "icon", "brand_pixmap", "has_fonts", "GRID", "SONG_CHAR"]
