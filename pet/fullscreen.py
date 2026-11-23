"""Keep the pet off monitors whose visible workspace contains a fullscreen window."""
import json
import os
from pathlib import Path
import socket

from gi.repository import Gio, GLib


EVENTS = {
    b"fullscreen", b"workspacev2", b"focusedmonv2", b"activespecialv2",
    b"movewindowv2", b"moveworkspacev2", b"openwindow", b"closewindow",
    b"monitoraddedv2", b"monitorremovedv2", b"configreloaded",
}


def covered_origins(output):
    decoder = json.JSONDecoder()
    monitors, offset = decoder.raw_decode(output.lstrip())
    clients, _ = decoder.raw_decode(output.lstrip()[offset:].lstrip())
    fullscreen = {
        (client["monitor"], client["workspace"]["name"])
        for client in clients if client["mapped"] and not client["hidden"] and client["fullscreen"] & 2
    }
    return {
        (monitor["x"], monitor["y"])
        for monitor in monitors
        if any((monitor["id"], monitor[workspace]["name"]) in fullscreen
               for workspace in ("activeWorkspace", "specialWorkspace"))
    }


class FullscreenWatch:
    def __init__(self, update, config_reloaded):
        self.update = update
        self.config_reloaded = config_reloaded
        self.enabled = False
        self.stream = None
        self.stream_watch = 0
        self.timer = 0
        self.request = None
        self.pending = False
        self.buffer = b""

    def start(self):
        if self.enabled:
            self.refresh()
            return
        self.enabled = True
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if runtime and signature:
            self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self.stream.settimeout(0.1)
                self.stream.connect(str(Path(runtime) / "hypr" / signature / ".socket2.sock"))
                self.stream.setblocking(False)
                self.stream_watch = GLib.io_add_watch(
                    self.stream.fileno(), GLib.PRIORITY_DEFAULT,
                    GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                    self.on_event,
                )
            except OSError:
                self.stream.close()
                self.stream = None
        if not self.stream:
            self.timer = GLib.timeout_add_seconds(1, self.poll)
        self.refresh()

    def stop(self):
        self.enabled = False
        self.pending = False
        self.buffer = b""
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.stream_watch:
            GLib.source_remove(self.stream_watch)
            self.stream_watch = 0
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.request:
            self.request.cancel()
            self.request = None

    def poll(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def on_event(self, _fd, condition):
        if condition & GLib.IOCondition.IN:
            try:
                data = self.stream.recv(65536)
            except BlockingIOError:
                return GLib.SOURCE_CONTINUE
            except OSError:
                data = b""
            if data:
                lines = (self.buffer + data).split(b"\n")
                self.buffer = lines.pop()
                events = {line.partition(b">>")[0] for line in lines}
                if b"configreloaded" in events:
                    self.config_reloaded()
                if events & EVENTS:
                    self.refresh()
                return GLib.SOURCE_CONTINUE
        self.stream.close()
        self.stream = None
        self.stream_watch = 0
        self.timer = GLib.timeout_add_seconds(1, self.poll)
        return GLib.SOURCE_REMOVE

    def refresh(self):
        if not self.enabled:
            return
        if self.request:
            self.pending = True
            return
        try:
            process = Gio.Subprocess.new(
                ["hyprctl", "-j", "--batch", "monitors; clients"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            return
        cancellation = self.request = Gio.Cancellable()

        def done(process, result):
            if self.request != cancellation:
                return
            self.request = None
            try:
                _, output, _ = process.communicate_utf8_finish(result)
                if process.get_successful():
                    self.update(covered_origins(output))
            except (GLib.Error, ValueError, KeyError, TypeError):
                pass
            if self.pending:
                self.pending = False
                self.refresh()

        process.communicate_utf8_async(None, cancellation, done)
