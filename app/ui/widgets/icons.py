"""
Crisp vector icons (lucide-style strokes) rendered via QSvgRenderer.
icon(kind, color, size) -> QIcon; pixmap(kind, color, size) -> QPixmap.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    # stroke icons, 24x24 grid, stroke=2 round caps (lucide geometry)
    "new": '<path d="M12 5v14M5 12h14"/>',
    "dashboard": '<path d="M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z"/>',
    "projects": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "templates": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
    "settings": '<circle cx="12" cy="12" r="3.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3M17 6l3 3M14.5 8.5l2 2"/>',
    "logs": '<path d="M4 5h16M4 10h16M4 15h10M4 20h7"/>',
    "sources": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "links": '<path d="M10 14a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 10a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10M15 10v10"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h13M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "play": '<path d="M7 4.5v15l12-7.5Z"/>',
    "export": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "pause": '<path d="M9 5v14M15 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M8 3v5h6V3M8 21v-6h8v6"/>',
    "chev": '<path d="M9 6l6 6-6 6"/>',
    "eye": '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="3"/>',
}

_cache: dict = {}


def device_pixel_ratio() -> float:
    """Render icon pixmaps densely enough for every connected display."""
    app = QGuiApplication.instance()
    ratios = [screen.devicePixelRatio() for screen in app.screens()] if app else []
    # A 2x source avoids enlarging tiny 16-20px rasters on common 125-200%
    # Windows scaling; 3x is ample for higher-density monitors as well.
    return min(3.0, max([2.0, *ratios]))


def pixmap(kind: str, color: str, size: int = 20) -> QPixmap:
    dpr = device_pixel_ratio()
    key = (kind, color, size, dpr)
    if key in _cache:
        return _cache[key]
    body = _P.get(kind, _P["new"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="' + color + '" stroke-width="1.9" stroke-linecap="round" '
        'stroke-linejoin="round">' + body + "</svg>"
    )
    renderer = QSvgRenderer(svg.encode())
    pixel_size = max(1, round(size * dpr))
    pm = QPixmap(pixel_size, pixel_size)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0.0, 0.0, float(size), float(size)))
    p.end()
    _cache[key] = pm
    return pm


def icon(kind: str, color: str, size: int = 20) -> QIcon:
    return QIcon(pixmap(kind, color, size))
