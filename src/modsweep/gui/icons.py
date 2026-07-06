"""Painted icons: deterministic on every platform, unlike emoji fonts.

Icons are not themed, so fixed colors are fine here (the one place the
no-hardcoded-colors rule does not apply).
"""

from __future__ import annotations

import functools

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _app_icon() -> QIcon:
    """A painted broom: deterministic on every platform, unlike emoji fonts.

    Icons are not themed, so fixed colors are fine here (the one place the
    no-hardcoded-colors rule does not apply).
    """
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw the broom upright - which makes "handle runs straight into the
    # ferrule's center" trivially true - then tilt the whole painter for the
    # mid-sweep pose. The rotation cannot break the joint geometry.
    painter.save()
    painter.translate(150, 122)
    painter.rotate(32)  # clockwise: handle to upper right, bristles lower left
    painter.translate(-128, -122)
    painter.setPen(QPen(QColor("#8a5a2b"), 20, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    painter.drawLine(128, 6, 128, 122)  # handle, taller than the head
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#c9a227")))
    painter.drawRoundedRect(128 - 24, 114, 48, 28, 7, 7)  # ferrule
    fan = QPainterPath(QPointF(108, 136))  # bristles dragging into the sweep
    fan.cubicTo(QPointF(70, 160), QPointF(40, 190), QPointF(46, 214))
    fan.quadTo(QPointF(90, 230), QPointF(134, 214))
    fan.quadTo(QPointF(158, 178), QPointF(148, 136))
    fan.closeSubpath()
    painter.setBrush(QBrush(QColor("#d9b45b")))
    painter.drawPath(fan)
    painter.setPen(QPen(QColor("#a87f31"), 6, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for start, control, end in (
        ((113, 146), (107, 194), (60, 206)),
        ((126, 148), (131, 194), (90, 216)),
        ((138, 146), (150, 186), (120, 212)),
    ):
        strand = QPainterPath(QPointF(*start))
        strand.quadTo(QPointF(*control), QPointF(*end))
        painter.drawPath(strand)  # strands bow with the fan's swoop
    painter.restore()

    # Dust rings on the opposite side of the sweep, level with the bristles.
    painter.setPen(QPen(QColor("#9aa0a6"), 7))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(208, 166), 10, 10)
    painter.drawEllipse(QPointF(188, 202), 6, 6)
    painter.drawEllipse(QPointF(228, 202), 7, 7)
    painter.end()
    return QIcon(pixmap)


def _paint_icon(draw) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


@functools.cache
def _pin_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(QPen(QColor("#8a5a2b"), 3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(16, 16, 7, 27)  # needle
        p.setPen(QPen(QColor("#7a6210"), 2))
        p.setBrush(QBrush(QColor("#e3b341")))
        p.drawEllipse(14, 4, 13, 13)  # head

    return _paint_icon(draw)


@functools.cache
def _ban_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(QPen(QColor("#c0392b"), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(5, 5, 22, 22)
        p.drawLine(9, 23, 23, 9)

    return _paint_icon(draw)


@functools.cache
def _lock_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(QPen(QColor("#8c8c8c"), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(9, 3, 14, 16, 0, 180 * 16)  # shackle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#9a9a9a")))
        p.drawRoundedRect(7, 13, 18, 14, 3, 3)  # body

    return _paint_icon(draw)
