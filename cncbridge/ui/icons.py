"""
Small stroke icons drawn on a canvas, so that the sidebar needs no image files and the icons stay sharp at any
window scale. Each icon is a list of strokes on a 24 x 24 grid:

    ("line", [(x, y), ...])        a line through these points
    ("circle", x, y, radius)       an outline
    ("rect", x1, y1, x2, y2)       an outline
"""
import math


def _spokes(cx, cy, inner, outer, count):
    return [("line", [(cx + inner * math.cos(a), cy + inner * math.sin(a)),
                      (cx + outer * math.cos(a), cy + outer * math.sin(a))])
            for a in (2 * math.pi * i / count for i in range(count))]


ICONS = {
    "logo": [("line", [(4, 7), (14, 7)]), ("line", [(4, 12), (20, 12)]), ("line", [(4, 17), (14, 17)]),
             ("line", [(17, 4), (20, 7), (17, 10)])],
    "monitor": [("line", [(3, 12), (7, 12), (10, 4), (14, 20), (17, 12), (21, 12)])],
    "setup": [("line", [(4, 21), (4, 14)]), ("line", [(4, 10), (4, 3)]), ("line", [(12, 21), (12, 12)]),
              ("line", [(12, 8), (12, 3)]), ("line", [(20, 21), (20, 16)]), ("line", [(20, 12), (20, 3)]),
              ("line", [(1, 14), (7, 14)]), ("line", [(9, 8), (15, 8)]), ("line", [(17, 16), (23, 16)])],
    "pick": [("rect", 3, 3, 21, 21), ("line", [(3, 9), (21, 9)]), ("line", [(3, 15), (21, 15)]),
             ("line", [(9, 3), (9, 21)]), ("line", [(15, 3), (15, 21)])],
    "delivery": [("line", [(22, 2), (15, 22), (11, 13), (2, 9), (22, 2)]), ("line", [(22, 2), (11, 13)])],
    "history": [("circle", 12, 12, 9), ("line", [(12, 7), (12, 12), (15, 14)])],
    "operator": [("circle", 12, 8, 4), ("line", [(4, 21), (5, 17.5), (8, 15), (12, 14), (16, 15), (19, 17.5), (20, 21)])],
    "settings": [("circle", 12, 12, 3), ("circle", 12, 12, 7)] + _spokes(12, 12, 7, 10, 8),
    "help": [("circle", 12, 12, 9), ("line", [(9.5, 9.5), (10.5, 8), (12, 7.5), (13.5, 8), (14.5, 9.5), (14.2, 11),
                                              (12.5, 12.3), (12, 13.5)]), ("line", [(12, 16.3), (12, 16.6)])],
    "warning": [("line", [(12, 3), (22.5, 20.5), (1.5, 20.5), (12, 3)]), ("line", [(12, 9.5), (12, 14)]),
                ("line", [(12, 17.2), (12, 17.5)])],
    "log": [("line", [(5, 6), (19, 6)]), ("line", [(5, 11), (19, 11)]), ("line", [(5, 16), (13, 16)])],
}


def draw(canvas, name, colour, size):
    """Draw an icon filling a size x size pixel canvas, in this colour."""
    scale = size / 24
    width = max(1.5, 2 * scale)
    canvas.delete("all")
    for kind, *args in ICONS[name]:
        if kind == "line":
            points = [value * scale for point in args[0] for value in point]
            canvas.create_line(*points, fill=colour, width=width, capstyle="round", joinstyle="round")
        elif kind == "circle":
            x, y, r = (value * scale for value in args)
            canvas.create_oval(x - r, y - r, x + r, y + r, outline=colour, width=width)
        elif kind == "rect":
            canvas.create_rectangle(*(value * scale for value in args), outline=colour, width=width)
