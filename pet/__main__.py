import json
import math
import os
from dataclasses import asdict, replace
from pathlib import Path
import shutil
import signal
import sys
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
except (ImportError, ValueError):
    GtkLayerShell = None

from . import paint, voice
from .appearance import Appearance, BACKGROUNDS, PALETTE
from .fullscreen import FullscreenWatch
from .layout import (
    ACTIONS, COLORS, CONTROLS, SETTINGS, STYLE, PET_RADIUS,
    button_frames, button_offsets, clamp, follow_targets, pet_position,
)

NAMESPACE = "hyprpet"
BLUR_NAMESPACE = "hyprpet-glass"
BLUR_RULE = (
    'if not _G.hyprpet_glass then _G.hyprpet_glass = hl.layer_rule({'
    f'name="{BLUR_NAMESPACE}", match={{namespace="^{BLUR_NAMESPACE}$"}}, '
    'blur=true, ignore_alpha=0.03, no_anim=true}) end'
)
POSITION_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / NAMESPACE / "positions.json"
PREFERENCES_FILE = POSITION_FILE.with_name("preferences.json")
COMMANDS = {
    "quieter": [sys.executable, "-m", "pet.audio", "quieter"],
    "louder": [sys.executable, "-m", "pet.audio", "louder"],
    "yandex-wave": [sys.executable, "-m", "pet.yandex", "--yandex-wave"],
    "yandex-favorites": [sys.executable, "-m", "pet.yandex", "--yandex-favorites"],
    "yandex-jealous": [sys.executable, "-m", "pet.yandex", "--yandex-track", "150139411:41528093"],
}
PLAYER_ACTIONS = {"previous", "play-pause", "next", "pause", "play"}
FEEDBACK = {**ACTIONS, "play": "Воспроизведение", "pause": "Пауза", "yandex-jealous": "Jealous"}
FILL_DURATION = 0.42


def launch_process(argv):
    return Gio.Subprocess.new(
        argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
    )


def write_config(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(path)
    except OSError as exc:
        print(f"Pet: {exc}", file=sys.stderr)


class Overlay(Gtk.ApplicationWindow):
    def __init__(self, app, monitor, key):
        super().__init__(application=app, title="Pet", decorated=False, accept_focus=False)
        self.app = app
        self.monitor = monitor
        self.compact = not app.use_layer_shell
        self.key = key
        self.position = app.positions.get(key)
        self.x = self.y = 0
        self.hover = None
        self.pointer = None
        self.pressed = None
        self.drag_origin = None
        self.drag_position = None
        self.dragging = False
        self.opened = False
        self.menu = CONTROLS
        self.pending_menu = None
        self.target_key = None
        self.target_origin = None
        self.targets = ()
        self.offsets = None
        self.progress = 0
        self.frames = []
        self.status = ""
        self.status_until = 0
        self.hover_since = 0
        self.hover_anchor = None
        self.last_tick = 0
        self.bounds = None
        self.input_shape = None

        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        if self.compact:
            self.setup_desktop_window()
        else:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, app.layer_namespace())
            self.set_fullscreen_layer()
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_exclusive_zone(self, -1)
            GtkLayerShell.set_monitor(self, monitor)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self, edge, True)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("size-allocate", self.resize_overlay)
        self.connect("draw", self.render)
        self.connect("motion-notify-event", self.motion)
        self.connect("leave-notify-event", self.leave)
        self.connect("button-press-event", self.press)
        self.connect("button-release-event", self.release)
        self.add_tick_callback(self.tick)

    def setup_desktop_window(self):
        self.set_resizable(False)
        self.set_focus_on_map(False)
        geometry = self.monitor.get_workarea() if self.app.x11 else self.monitor.get_geometry()
        width, height = min(340, geometry.width), min(340, geometry.height)
        self.set_default_size(width, height)
        if self.app.x11:
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.stick()
            self.move(geometry.x + geometry.width - width, geometry.y + geometry.height - height)
            self.set_fullscreen_layer()

    def set_fullscreen_layer(self):
        if self.compact:
            if self.app.x11:
                self.set_keep_above(self.app.above_fullscreen)
            return
        layer = GtkLayerShell.Layer.OVERLAY if self.app.above_fullscreen else GtkLayerShell.Layer.TOP
        if GtkLayerShell.get_layer(self) != layer:
            visible = self.get_visible()
            if visible:
                self.hide()
            GtkLayerShell.set_layer(self, layer)
            self.input_shape = None
            if visible:
                self.show_all()

    def sync_namespace(self):
        if self.compact:
            return
        namespace = self.app.layer_namespace()
        if GtkLayerShell.get_namespace(self) == namespace:
            return
        visible = self.get_visible()
        if visible:
            self.hide()
        GtkLayerShell.set_namespace(self, namespace)
        self.input_shape = None
        if visible:
            self.show_all()

    def set_covered(self, covered):
        if covered:
            self.opened = False
            self.pending_menu = None
            self.pressed = self.drag_origin = self.pointer = None
            self.drag_position = None
            self.dragging = False
            self.hide()
        elif not self.get_visible():
            self.input_shape = None
            self.show_all()

    def resize_overlay(self, _widget, allocation):
        w, h = allocation.width, allocation.height
        if self.compact:
            x, y = w / 2, h / 2
        elif self.position is None:
            x, y = w - 52, h - 52
        else:
            x, y = self.position[0] * w, self.position[1] * h
        self.x, self.y = pet_position(x, y, w, h)
        self.input_shape = None
        self.queue_draw()

    def hit(self, x, y):
        for action, bx, by, radius, opacity in reversed(self.frames):
            if self.opened and opacity >= 0.65 and math.hypot(x - bx, y - by) <= radius + 2:
                return action
        if math.hypot(x - self.x, y - self.y) <= PET_RADIUS + 3:
            return "pet"
        return None

    def refresh_hover(self, seconds):
        hit = self.hit(*self.pointer) if self.pointer else None
        if hit != self.hover:
            self.hover, self.hover_since = hit, seconds
            self.hover_anchor = next(((x, y) for action, x, y, r, a in self.frames if action == hit), None)
            self.get_window().set_cursor(self.app.hand if hit else None)

    def motion(self, _widget, event):
        self.pointer = event.x, event.y
        if self.drag_origin:
            sx, sy, px, py = self.drag_origin
            if self.dragging or math.hypot(event.x - sx, event.y - sy) > 5:
                if self.compact:
                    self.pressed = self.drag_origin = None
                    self.begin_move_drag(1, round(event.x_root), round(event.y_root), event.time)
                    return True
                self.dragging = True
                self.hover_anchor = None
                self.drag_position = px + event.x - sx, py + event.y - sy
        return True

    def place_dragged_pet(self):
        if self.drag_position:
            w, h = self.get_allocated_width(), self.get_allocated_height()
            self.x, self.y = pet_position(*self.drag_position, w, h)
            self.position = self.x / w, self.y / h
            self.drag_position = None

    def leave(self, _widget, _event):
        self.pointer = None
        return True

    def press(self, _widget, event):
        target = self.hit(event.x, event.y)
        if event.button == 1:
            self.pressed = target
            if target == "pet":
                self.drag_origin = event.x, event.y, self.x, self.y
        elif event.button == 3 and target == "pet":
            if self.opened and self.menu == self.app.settings_menu:
                self.opened = False
                self.pending_menu = None
            else:
                self.open_menu(self.app.settings_menu)
        return bool(target)

    def release(self, _widget, event):
        if event.button != 1:
            return False
        if self.dragging:
            self.place_dragged_pet()
            self.app.save_position(self.key, self.position)
        elif self.pressed and self.pressed == self.hit(event.x, event.y):
            if self.pressed == "pet":
                if self.opened or self.pending_menu:
                    self.opened = False
                    self.pending_menu = None
                else:
                    self.open_menu(CONTROLS)
                self.status = ""
            else:
                self.app.activate_action(self, self.pressed)
        self.pressed = self.drag_origin = None
        self.dragging = False
        return True

    def open_menu(self, menu):
        if self.menu != menu and self.progress > 0:
            self.pending_menu = menu
            self.opened = False
        else:
            self.menu = menu
            self.opened = True
        self.hover_anchor = None

    def say(self, message):
        self.status = message
        self.status_until = time.monotonic() + 2.6

    def caption(self, seconds):
        if self.status and seconds < self.status_until:
            return self.status, self.x, self.y, 34
        if self.hover_anchor and seconds - self.hover_since > 0.32:
            return self.app.action_label(self.hover), *self.hover_anchor, 24
        return None

    def tick(self, _widget, frame_clock):
        seconds = frame_clock.get_frame_time() / 1_000_000
        elapsed = seconds - self.last_tick
        animated = self.opened or self.progress or self.app.voice_animating(seconds)
        menu_in_motion = self.progress != float(self.opened)
        if (not menu_in_motion and (not self.dragging or self.drag_position is None)
                and elapsed < (0.015 if animated else 0.031)):
            return True
        self.last_tick = seconds
        self.place_dragged_pet()
        delta = min(elapsed, 0.05) / (0.54 if self.opened else 0.32)
        self.progress = clamp(self.progress + (delta if self.opened else -delta), 0, 1)
        if self.progress == 0 and self.pending_menu:
            self.menu, self.pending_menu = self.pending_menu, None
            self.opened = True
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if self.progress:
            key = w, h, self.menu
            if key != self.target_key:
                self.targets = ()
                self.offsets = None
                self.target_origin = self.x, self.y
                self.target_key = key
            movement = self.x - self.target_origin[0], self.y - self.target_origin[1]
            if movement != (0, 0) or self.offsets is None:
                self.offsets = button_offsets(self.x, self.y, w, h, len(self.menu), self.offsets)
                self.target_origin = self.x, self.y
            self.targets = follow_targets(self.x, self.y, w, h, self.offsets, self.targets,
                                          movement, min(elapsed, 0.05))
            self.frames = list(button_frames(self.x, self.y, w, h, self.progress, seconds, self.menu, self.targets))
        else:
            self.target_key = None
            self.frames = []
        if not self.dragging:
            self.refresh_hover(seconds)
            self.update_input_shape()

        points = [(self.x, self.y, PET_RADIUS + 15)]
        points.extend((x, y, r + 3) for _, x, y, r, a in self.frames if a > 0)
        caption = self.caption(seconds)
        if caption:
            _, x, y, offset = caption
            points.extend(((x - 260, y, offset + 34), (x + 260, y, offset + 34)))
        x1 = max(0, math.floor(min(x - r for x, y, r in points)))
        y1 = max(0, math.floor(min(y - r for x, y, r in points)))
        x2 = min(w, math.ceil(max(x + r for x, y, r in points)))
        y2 = min(h, math.ceil(max(y + r for x, y, r in points)))
        if self.bounds and self.bounds != (x1, y1, x2, y2):
            left, top, right, bottom = self.bounds
            self.queue_draw_area(left, top, right - left, bottom - top)
        self.bounds = x1, y1, x2, y2
        self.queue_draw_area(x1, y1, x2 - x1, y2 - y1)
        return True

    def update_input_shape(self):
        hotspots = [(round(self.x), round(self.y), PET_RADIUS + 3)]
        hotspots.extend((round(x), round(y), round(r) + 2)
                        for _, x, y, r, a in self.frames if a >= 0.65 and self.opened)
        if hotspots == self.input_shape:
            return
        region = cairo.Region()
        for x, y, radius in hotspots:
            for dy in range(-radius, radius + 1, 2):
                dx = math.ceil(math.sqrt(radius * radius - dy * dy))
                region.union(cairo.RectangleInt(x - dx, y + dy, 2 * dx + 1, 2))
        self.get_window().input_shape_combine_region(region, 0, 0)
        if self.app.x11 and not self.get_screen().is_composited():
            self.get_window().shape_combine_region(region, 0, 0)
        self.input_shape = hotspots

    def render(self, _widget, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        seconds = time.monotonic()
        for action, x, y, radius, opacity in self.frames:
            paint.button(cr, action, x, y, radius, opacity,
                         self.hover == action, self.app.action_enabled(action), self.app.appearance)
        paint.pet(cr, self.x, self.y, seconds, self.hover == "pet",
                  self.app.voice_fill(seconds), self.app.voice_icon(seconds),
                  self.dragging, self.app.appearance)
        caption = self.caption(seconds)
        if caption:
            message, x, y, offset = caption
            paint.label(cr, message, x, y, self.get_allocated_width(), self.get_allocated_height(), offset)
        return True


class PetApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.hyprpet.Overlay",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Закрыть Pet", None)
        self.add_main_option("fullscreen", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Видимость в полноэкранном режиме", "show|hide|toggle")
        self.add_main_option("voice", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Прослушивание ключевого слова", "on|off|toggle")
        self.started = False
        self.use_layer_shell = False
        self.x11 = False
        self.hyprland = False
        self.settings_menu = SETTINGS
        self.overlays = {}
        self.above_fullscreen = True
        self.voice_enabled = True
        self.voice_process = None
        self.voice_ready = False
        self.voice_stopping = False
        self.voice_retry = 0
        self.wake_timer = 0
        self.voice_cycle = 0
        self.fill_from = self.fill_target = self.fill_changed_at = 0
        self.return_at = 0
        self.feedback_action = None
        self.feedback_at = 0
        self.covered = set()
        self.fullscreen_watch = FullscreenWatch(self.update_coverage, self.register_blur)
        self.appearance = Appearance()
        self.blur_ready = False
        self.blur_request = None
        try:
            preferences = json.loads(PREFERENCES_FILE.read_text())
            if isinstance(preferences, dict):
                self.above_fullscreen = preferences.get("above_fullscreen") is not False
                self.voice_enabled = preferences.get("voice_enabled") is not False
                style = preferences.get("appearance")
                if isinstance(style, dict):
                    background, border, accent = style.get("background"), style.get("border"), style.get("accent")
                    self.appearance = Appearance(
                        background if background in tuple(BACKGROUNDS) else self.appearance.background,
                        border if isinstance(border, bool) else self.appearance.border,
                        accent if accent in tuple(PALETTE) else self.appearance.accent,
                    )
        except (OSError, ValueError):
            pass
        self.positions = {}
        try:
            saved = json.loads(POSITION_FILE.read_text())
            if isinstance(saved, dict):
                self.positions = {
                    key: position for key, position in saved.items()
                    if isinstance(position, list) and len(position) == 2
                    and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in position)
                }
        except (OSError, ValueError):
            pass

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        fullscreen = options.lookup_value("fullscreen", None)
        voice_mode = options.lookup_value("voice", None)
        if fullscreen and fullscreen.get_string() not in ("show", "hide", "toggle"):
            command_line.printerr_literal("Допустимые значения --fullscreen: show, hide, toggle\n")
            return 2
        if voice_mode and voice_mode.get_string() not in ("on", "off", "toggle"):
            command_line.printerr_literal("Допустимые значения --voice: on, off, toggle\n")
            return 2
        if options.contains("quit"):
            self.stop()
            return 0
        if fullscreen:
            mode = fullscreen.get_string()
            self.set_fullscreen(not self.above_fullscreen if mode == "toggle" else mode == "show")
        if voice_mode:
            mode = voice_mode.get_string()
            self.set_voice(not self.voice_enabled if mode == "toggle" else mode == "on")
        self.activate()
        return 0

    def do_activate(self):
        if self.started:
            return
        display = Gdk.Display.get_default()
        if not display:
            print("Pet: графический сеанс недоступен.", file=sys.stderr)
            return
        backend = display.__gtype__.name
        self.x11 = backend == "GdkX11Display"
        self.use_layer_shell = (
            backend == "GdkWaylandDisplay" and GtkLayerShell is not None and GtkLayerShell.is_supported()
        )
        desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
        self.hyprland = (self.use_layer_shell and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
                         and bool(shutil.which("hyprctl")) and ("hyprland" in desktops or desktops == [""]))
        if not self.use_layer_shell and not self.x11:
            self.settings_menu = tuple(action for action in SETTINGS if action != "fullscreen")
        self.hold()
        self.started = True
        self.hand = Gdk.Cursor.new_from_name(display, "pointer")
        self.watch_compositor()
        self.register_blur()
        for i in range(display.get_n_monitors()):
            self.add_monitor(display, display.get_monitor(i))
        display.connect("monitor-added", self.add_monitor)
        display.connect("monitor-removed", self.remove_monitor)
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, self.stop)
        self.update_voice()

    def add_monitor(self, _display, monitor):
        if not self.x11 and not self.use_layer_shell and self.overlays:
            return
        base = f"{monitor.get_manufacturer() or ''}/{monitor.get_model() or ''}"
        used = {overlay.key for overlay in self.overlays.values()}
        key = base
        index = 1
        while key in used:
            index += 1
            key = f"{base}/{index}"
        overlay = self.overlays[monitor] = Overlay(self, monitor, key)
        monitor.connect("notify::geometry", self.monitor_geometry_changed)
        self.apply_coverage(overlay)
        self.fullscreen_watch.refresh()

    def remove_monitor(self, _display, monitor):
        overlay = self.overlays.pop(monitor, None)
        if overlay:
            monitor.disconnect_by_func(self.monitor_geometry_changed)
            overlay.destroy()
        if not self.overlays and _display.get_n_monitors():
            self.add_monitor(_display, _display.get_monitor(0))

    def save_position(self, key, position):
        self.positions[key] = position
        write_config(POSITION_FILE, self.positions)

    def save_preferences(self):
        write_config(PREFERENCES_FILE, {
            "above_fullscreen": self.above_fullscreen,
            "voice_enabled": self.voice_enabled,
            "appearance": asdict(self.appearance),
        })

    def monitor_geometry_changed(self, monitor, _property):
        if monitor in self.overlays:
            self.apply_coverage(self.overlays[monitor])

    def apply_coverage(self, overlay):
        geometry = overlay.monitor.get_geometry()
        overlay.set_covered(not self.above_fullscreen and (geometry.x, geometry.y) in self.covered)

    def update_coverage(self, covered):
        self.covered = covered
        for overlay in self.overlays.values():
            self.apply_coverage(overlay)

    def set_fullscreen(self, enabled):
        self.above_fullscreen = enabled
        self.save_preferences()
        if not self.started:
            return
        for overlay in self.overlays.values():
            overlay.set_fullscreen_layer()
        if enabled:
            self.update_coverage(set())
        self.watch_compositor()

    def watch_compositor(self):
        if self.hyprland and (not self.above_fullscreen or self.appearance.translucent):
            self.fullscreen_watch.start()
        else:
            self.fullscreen_watch.stop()

    def layer_namespace(self):
        return BLUR_NAMESPACE if self.appearance.translucent and self.blur_ready else NAMESPACE

    def register_blur(self):
        if not self.hyprland or not self.appearance.translucent or self.blur_request:
            return
        try:
            self.blur_request = launch_process(["hyprctl", "eval", BLUR_RULE])
        except GLib.Error:
            self.say(None, "Размытие Hyprland недоступно")
            return

        def done(process, result):
            self.blur_request = None
            try:
                _, output, _ = process.communicate_utf8_finish(result)
                self.blur_ready = process.get_successful() and output.strip() == "ok"
            except GLib.Error:
                self.blur_ready = False
            if self.appearance.translucent:
                if not self.blur_ready:
                    self.say(None, "Размытие Hyprland недоступно")
                for overlay in self.overlays.values():
                    overlay.sync_namespace()

        self.blur_request.communicate_utf8_async(None, None, done)

    def change_appearance(self, **options):
        old_background = self.appearance.background
        self.appearance = replace(self.appearance, **options)
        self.save_preferences()
        if self.appearance.background != old_background:
            self.watch_compositor()
            if self.appearance.translucent:
                self.register_blur()
            for overlay in self.overlays.values():
                overlay.sync_namespace()

    def action_enabled(self, action):
        return (action == "voice" and self.voice_ready or
                action == "fullscreen" and self.above_fullscreen or
                action == "border" and self.appearance.border or
                action == self.appearance.background or action == self.appearance.accent)

    def action_label(self, action):
        if action == "voice":
            return "Отключить голосовое управление" if self.voice_enabled else "Включить слово «Пэт»"
        if action == "fullscreen":
            if self.x11:
                return "Выключить показ поверх окон" if self.above_fullscreen else "Показывать поверх окон"
            return "Скрывать пэта в полноэкранном режиме" if self.above_fullscreen else "Показывать поверх полноэкранных окон"
        if action == "background-blur" and not self.hyprland:
            return "Полупрозрачный фон"
        if action == "border":
            return "Убрать обводку" if self.appearance.border else "Вернуть обводку"
        return ACTIONS[action]

    def say(self, overlay, message):
        if overlay:
            overlay.say(message)
        else:
            for pet in self.overlays.values():
                pet.say(message)

    def activate_action(self, overlay, action):
        cycle = self.voice_cycle if overlay is None else None
        if action == "voice":
            self.set_voice(not self.voice_enabled)
            if self.voice_enabled and self.voice_ready:
                overlay.say("Скажи «Пэт»")
        elif action == "fullscreen":
            self.set_fullscreen(not self.above_fullscreen)
            if self.x11:
                overlay.say("Поверх окон" if self.above_fullscreen else "Обычное окно")
            else:
                overlay.say("Поверх полноэкранных окон" if self.above_fullscreen else "Скрывается в полноэкранном режиме")
        elif action == "settings":
            overlay.open_menu(self.settings_menu)
        elif action == "appearance":
            overlay.open_menu(STYLE)
        elif action == "accent":
            overlay.open_menu(COLORS)
        elif action in BACKGROUNDS:
            self.change_appearance(background=action)
        elif action == "border":
            self.change_appearance(border=not self.appearance.border)
        elif action in PALETTE:
            self.change_appearance(accent=action)
        elif action == "back":
            previous = {self.settings_menu: CONTROLS, STYLE: self.settings_menu, COLORS: STYLE}
            overlay.open_menu(previous.get(overlay.menu, CONTROLS))
        elif action == "quit":
            self.stop()
        elif action in PLAYER_ACTIONS:
            self.control_player(overlay, action, cycle)
        else:
            self.run_control(overlay, action, COMMANDS[action], cycle)

    def control_player(self, overlay, action, cycle):
        try:
            status = launch_process(["playerctl", "--all-players", "status",
                                     "--format", "{{status}} {{playerInstance}}"])
        except GLib.Error:
            self.action_result(overlay, action, cycle, error="Установи playerctl")
            return

        def select_player(process, result):
            try:
                _, output, _ = process.communicate_utf8_finish(result)
            except GLib.Error:
                output = ""
            players = []
            for state in ("Playing", "Paused", "Stopped"):
                prefix = state + " "
                players.extend(line[len(prefix):] for line in output.splitlines() if line.startswith(prefix))
            argv = ["playerctl"]
            if players:
                argv.append("--player=" + ",".join(players))
            self.run_control(overlay, action, [*argv, action], cycle)

        status.communicate_utf8_async(None, None, select_player)

    def run_control(self, overlay, action, argv, cycle):
        try:
            process = launch_process(argv)
        except GLib.Error:
            self.action_result(overlay, action, cycle, error=f"Установи {argv[0]}")
            return

        def done(process, result):
            try:
                _, _, errors = process.communicate_utf8_finish(result)
                if process.get_successful():
                    self.action_result(overlay, action, cycle)
                    return
            except GLib.Error:
                errors = ""
            if action.startswith("yandex-"):
                error = errors.strip() or "Яндекс Музыка недоступна"
            else:
                error = "Плеер недоступен" if argv[0] == "playerctl" else "Громкость недоступна"
            self.action_result(overlay, action, cycle, error=error)

        process.communicate_utf8_async(None, None, done)

    def action_result(self, overlay, action, cycle, error=None):
        if cycle is not None and cycle != self.voice_cycle:
            return
        if overlay is not None or error:
            self.say(overlay, error or FEEDBACK[action])
            return
        self.feedback_action = action
        self.feedback_at = max(time.monotonic(), self.return_at + 0.82 * FILL_DURATION)

    def voice_fill(self, seconds):
        progress = clamp((seconds - self.fill_changed_at) / FILL_DURATION, 0, 1)
        ease = progress * progress * (3 - 2 * progress)
        return self.fill_from + (self.fill_target - self.fill_from) * ease

    def voice_icon(self, seconds):
        age = seconds - self.feedback_at
        if self.feedback_action and 0 <= age < paint.ICON_DURATION:
            return self.feedback_action, age
        return None

    def voice_animating(self, seconds):
        return (seconds < self.fill_changed_at + FILL_DURATION or
                self.feedback_action is not None and seconds < self.feedback_at + paint.ICON_DURATION)

    def set_voice_fill(self, target):
        if target != self.fill_target:
            now = time.monotonic()
            self.fill_from = self.voice_fill(now)
            self.fill_changed_at = now
            self.fill_target = target

    def finish_wake(self, complete_fill=False):
        if self.wake_timer:
            GLib.source_remove(self.wake_timer)
            self.wake_timer = 0
        if complete_fill and self.fill_target == 1:
            full_at = self.fill_changed_at + FILL_DURATION
            remaining = full_at - time.monotonic()
            if remaining > 0:
                self.return_at = full_at
                self.wake_timer = GLib.timeout_add(math.ceil(remaining * 1000), self.wake_timed_out)
                return
        self.set_voice_fill(0)
        self.return_at = self.fill_changed_at

    def wake_timed_out(self):
        self.wake_timer = 0
        self.finish_wake()
        return GLib.SOURCE_REMOVE

    def set_voice(self, enabled):
        self.voice_enabled = enabled
        self.save_preferences()
        if not self.started:
            return
        if enabled:
            self.update_voice()
        else:
            self.stop_voice()

    def stop_voice(self):
        self.voice_ready = False
        self.voice_cycle += 1
        self.feedback_action = None
        self.finish_wake()
        if self.voice_retry:
            GLib.source_remove(self.voice_retry)
            self.voice_retry = 0
        if self.voice_process and not self.voice_stopping:
            self.voice_stopping = True
            self.voice_process.send_signal(signal.SIGTERM)

    def retry_voice(self):
        self.voice_retry = 0
        self.update_voice()
        return GLib.SOURCE_REMOVE

    def update_voice(self):
        if not self.voice_enabled or self.voice_process or self.voice_retry:
            return
        if not voice.PYTHON.is_file() or not (voice.MODEL / "am/final.mdl").is_file():
            self.say(None, "Для голоса запусти ./setup-voice")
            return
        try:
            process = Gio.Subprocess.new(
                [str(voice.PYTHON), "-m", "pet.voice"], Gio.SubprocessFlags.STDOUT_PIPE,
            )
        except GLib.Error:
            self.say(None, "Распознавание недоступно")
            return
        self.voice_process = process
        self.voice_stopping = False
        stream = Gio.DataInputStream.new(process.get_stdout_pipe())

        def read_line(stream, result):
            try:
                line, _ = stream.read_line_finish_utf8(result)
            except GLib.Error:
                line = None
            if line is None:
                stopped = self.voice_stopping
                self.voice_process = None
                self.voice_ready = self.voice_stopping = False
                self.finish_wake()
                if self.voice_enabled:
                    if stopped:
                        self.update_voice()
                    else:
                        self.say(None, "Микрофон недоступен, повторное подключение…")
                        self.voice_retry = GLib.timeout_add_seconds(5, self.retry_voice)
                return
            if not self.voice_stopping:
                try:
                    self.voice_event(json.loads(line))
                except ValueError:
                    pass
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

    def voice_event(self, event):
        kind = event.get("event")
        if kind == "ready":
            self.voice_ready = True
        elif kind == "wake":
            self.voice_cycle += 1
            self.feedback_action = None
            self.return_at = 0
            if self.wake_timer:
                GLib.source_remove(self.wake_timer)
            self.wake_timer = GLib.timeout_add(int(voice.FOLLOW_UP * 1000), self.wake_timed_out)
            self.set_voice_fill(1)
            for overlay in self.overlays.values():
                overlay.status = ""
        elif kind == "command":
            self.finish_wake(complete_fill=True)
            action = event.get("action")
            if action in COMMANDS or action in PLAYER_ACTIONS:
                self.activate_action(None, action)
        elif kind in ("search", "artist"):
            query = event.get("action")
            if isinstance(query, str) and query.strip():
                self.finish_wake(complete_fill=True)
                alternatives = event.get("alternatives", [])
                options = []
                if isinstance(alternatives, list):
                    options = [f"--alternative={name}" for name in alternatives[:2] if isinstance(name, str)]
                action = f"yandex-{kind}"
                self.run_control(None, action,
                                 [sys.executable, "-m", "pet.yandex", f"--{action}={query}", *options],
                                 self.voice_cycle)

    def stop(self):
        self.stop_voice()
        self.fullscreen_watch.stop()
        self.quit()
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    GLib.set_application_name("Pet")
    sys.exit(PetApp().run(sys.argv))
