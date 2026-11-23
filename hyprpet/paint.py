import math

import cairo

from .appearance import PALETTE
from .layout import BUTTON_RADIUS, PET_RADIUS, clamp

INK = (0.045, 0.045, 0.045)
EDGE = (0.49, 0.49, 0.49)
WHITE = (0.95, 0.95, 0.95)
BRIGHT = (1.0, 1.0, 1.0)
FONT = "Minecraft Rus"
FONT_OPTIONS = cairo.FontOptions()
FONT_OPTIONS.set_antialias(cairo.ANTIALIAS_GRAY)
ICON_DURATION = 1.25
GLASS_ALPHA = 0.46


def circle(cr, x, y, radius):
    cr.new_sub_path()
    cr.arc(x, y, radius, 0, math.tau)


def rounded(cr, x, y, w, h, radius):
    cr.new_sub_path()
    for cx, cy, angle in ((x+w-radius, y+radius, -0.5), (x+w-radius, y+h-radius, 0),
                          (x+radius, y+h-radius, 0.5), (x+radius, y+radius, 1)):
        cr.arc(cx, cy, radius, angle * math.pi, (angle + 0.5) * math.pi)
    cr.close_path()


def disk(cr, x, y, radius, style, edge, opacity):
    circle(cr, x, y, radius)
    cr.set_source_rgba(*INK, opacity * (GLASS_ALPHA if style.translucent else 1))
    cr.fill()
    if style.border:
        circle(cr, x, y, radius)
        cr.set_source_rgba(*edge, opacity * (0.70 if style.translucent else 1))
        cr.set_line_width(2)
        cr.stroke()


def pet(cr, x, y, seconds, hover, fill, feedback, dragging, style):
    y += 0 if dragging else 1.15 * math.sin(seconds * 1.8)
    radius = PET_RADIUS + (0.8 if hover else 0)
    inner = radius * (1 - fill)
    if inner > 0:
        circle(cr, x, y, inner)
        cr.set_source_rgba(*INK, GLASS_ALPHA if style.translucent else 1)
        cr.fill()
    if fill > 0:
        cr.save()
        circle(cr, x, y, radius)
        if inner > 0:
            circle(cr, x, y, inner)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.set_source_rgb(*BRIGHT)
        cr.fill()
        cr.restore()
    if style.border:
        circle(cr, x, y, radius)
        edge = tuple(v + (1 - v) * fill for v in EDGE)
        cr.set_source_rgba(*edge, 0.70 if style.translucent else 1)
        cr.set_line_width(2.5)
        cr.stroke()

    def content(color, accent):
        action, age = feedback if feedback else (None, -1)
        opacity = clamp(min(age / 0.18, (ICON_DURATION - age) / 0.25), 0, 1)
        opacity = opacity * opacity * (3 - 2 * opacity)
        if opacity < 1:
            prompt(cr, x, y, seconds, color, accent, 1 - opacity)
        if opacity > 0:
            cr.save()
            cr.translate(x, y + 3 * (1 - opacity))
            cr.scale(1.43, 1.43)
            icon(cr, action, accent, opacity)
            cr.restore()

    if fill == 0:
        content(WHITE, style.color)
        return
    if fill == 1:
        content(INK, INK)
        return
    # Use the same circle as the fill boundary to keep the prompt legible
    # while the white band travels inward or recedes toward the edge.
    cr.save()
    cr.rectangle(x - radius, y - radius, 2 * radius, 2 * radius)
    circle(cr, x, y, inner)
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.clip()
    content(INK, INK)
    cr.restore()
    cr.save()
    circle(cr, x, y, inner)
    cr.clip()
    content(WHITE, style.color)
    cr.restore()


def prompt(cr, x, y, seconds, color, accent, opacity):
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(2.35)
    cr.set_source_rgba(*color, opacity)
    cr.move_to(x - 10, y - 5)
    cr.line_to(x - 4, y)
    cr.line_to(x - 10, y + 5)
    cr.stroke()
    cursor = clamp((0.62 - seconds % 1.15) / 0.10, 0, 1)
    cr.set_source_rgba(*accent, opacity * cursor)
    cr.move_to(x + 1.5, y + 5.5)
    cr.line_to(x + 9.5, y + 5.5)
    cr.stroke()


def button(cr, action, x, y, radius, opacity, hovered, enabled, style):
    if opacity <= 0:
        return
    accent = style.color if enabled or hovered else WHITE
    edge = accent if enabled or hovered else EDGE
    disk(cr, x, y, radius + (0.8 if hovered else 0), style, edge, opacity)
    cr.save()
    cr.translate(x, y)
    cr.scale(radius / BUTTON_RADIUS, radius / BUTTON_RADIUS)
    if action in PALETTE:
        swatch = PALETTE[action][1]
        circle(cr, 0, 0, 5.4)
        cr.set_source_rgba(*swatch, opacity)
        cr.fill()
        if enabled:
            circle(cr, 0, 0, 8.3)
            cr.set_line_width(1.4)
            cr.stroke()
    elif action == "accent":
        for sx, sy, intensity in ((-4.2, 3, 0.60), (0, -4.2, 1), (4.2, 3, 0.82)):
            circle(cr, sx, sy, 2.45)
            cr.set_source_rgba(*style.color, opacity * intensity)
            cr.fill()
    else:
        icon(cr, action, accent, opacity, enabled)
    cr.restore()


def icon(cr, action, color, opacity, enabled=False):
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(1.9)
    cr.set_source_rgba(*color, opacity)

    def line(*points):
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.stroke()

    if action in ("next", "previous"):
        cr.save()
        if action == "previous":
            cr.scale(-1, 1)
        line((-6, -4.5), (-1.5, 0), (-6, 4.5))
        line((1.5, -4.5), (6, 0), (1.5, 4.5))
        cr.restore()
    elif action == "play-pause":
        line((-6.5, -5), (-6.5, 5), (0, 0), (-6.5, -5))
        line((3, -5), (3, 5))
        line((7, -5), (7, 5))
    elif action == "play":
        line((-4, -6), (-4, 6), (6, 0), (-4, -6))
    elif action == "pause":
        line((-3, -5.5), (-3, 5.5))
        line((3, -5.5), (3, 5.5))
    elif action == "yandex-wave":
        cr.move_to(-8, 1)
        cr.curve_to(-4.5, -7, -1.5, -7, 1, 0)
        cr.curve_to(3, 6, 6, 6, 8, 0)
        cr.stroke()
    elif action in ("yandex-jealous", "yandex-search", "yandex-artist"):
        line((-4, -6), (-4, 6), (6, 0), (-4, -6))
    elif action == "yandex-favorites":
        cr.move_to(0, 5.7)
        cr.curve_to(-2.4, 4, -7, 1, -7, -2.4)
        cr.curve_to(-7, -6.7, -2, -7.1, 0, -3.4)
        cr.curve_to(2, -7.1, 7, -6.7, 7, -2.4)
        cr.curve_to(7, 1, 2.4, 4, 0, 5.7)
        cr.stroke()
    elif action in ("quieter", "louder"):
        line((-7, -2.5), (-4.5, -2.5), (-0.5, -5.5), (-0.5, 5.5),
             (-4.5, 2.5), (-7, 2.5), (-7, -2.5))
        line((3, 0), (8, 0))
        if action == "louder":
            line((5.5, -2.5), (5.5, 2.5))
    elif action == "voice":
        rounded(cr, -2.7, -7.8, 5.4, 10.5, 2.7)
        cr.stroke()
        cr.move_to(-5.7, -0.3)
        cr.curve_to(-5.7, 7.4, 5.7, 7.4, 5.7, -0.3)
        cr.stroke()
        line((0, 5.5), (0, 8))
        if not enabled:
            line((-7, 6), (7, -6))
    elif action == "fullscreen":
        rounded(cr, -8, -6.4, 16, 12, 2.1)
        cr.stroke()
        line((-3, 8.2), (3, 8.2))
        if enabled:
            line((-3.5, -0.4), (-0.6, 2), (3.8, -2.6))
        else:
            line((-3, -0.3), (3, -0.3))
    elif action == "settings":
        for x, y, knob in ((-7, -4.5, -2.5), (-7, 4.5, 2.5)):
            line((x, y), (knob - 2.1, y))
            line((knob + 2.1, y), (7, y))
            circle(cr, knob, y, 2.1)
            cr.stroke()
    elif action in ("background-solid", "background-blur", "border"):
        if action == "background-solid":
            rounded(cr, -5.6, -5.6, 11.2, 11.2, 2)
            cr.fill()
        else:
            circle(cr, 0, 0, 6.5)
            if action == "border" and not enabled:
                cr.set_dash((2.2, 2.0))
            cr.stroke()
            cr.set_dash(())
            if action == "background-blur":
                line((-3.5, -2.5), (3.5, -2.5))
                line((-4.5, 0), (4.5, 0))
                line((-3.5, 2.5), (3.5, 2.5))
    elif action == "appearance":
        line((-7, 4), (-2, -5), (3, 4))
        line((-5, 0.5), (1, 0.5))
        line((5.5, -5), (5.5, 1))
        line((2.5, -2), (8.5, -2))
    elif action == "quit":
        cr.new_sub_path()
        cr.arc(0, 0.5, 6.5, -math.pi / 3, 4 * math.pi / 3)
        cr.stroke()
        line((0, -8), (0, -0.1))
    elif action == "back":
        line((-1, -5.5), (-6.3, 0), (-1, 5.5))
        line((-6.3, 0), (7, 0))


def label(cr, message, x, y, width, height, offset):
    cr.set_font_options(FONT_OPTIONS)
    cr.select_font_face(FONT)
    cr.set_font_size(12)
    extents = cr.text_extents(message)
    w, h = min(extents.width + 22, width - 12), 28
    left = clamp(x - w / 2, 6, width - w - 6)
    top = clamp(y - offset - h if y >= offset + h + 6 else y + offset, 6, height - h - 6)
    rounded(cr, left, top, w, h, 11)
    cr.set_source_rgb(*INK)
    cr.fill_preserve()
    cr.set_source_rgb(*EDGE)
    cr.set_line_width(1.6)
    cr.stroke()
    cr.save()
    cr.rectangle(left + 6, top, w - 12, h)
    cr.clip()
    cr.move_to(left + 11 - extents.x_bearing, top + (h - extents.height) / 2 - extents.y_bearing)
    cr.set_source_rgb(*WHITE)
    cr.show_text(message)
    cr.restore()
