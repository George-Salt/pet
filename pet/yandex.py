"""One-command, authenticated loopback bridge for the PulseSync add-on."""
import argparse
import hmac
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
TOKEN_FILE = CONFIG / "hyprpet" / "pulsesync-token"
PORT = 17442
TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")
TRACK_PATTERN = re.compile(r"([0-9]{1,18})(?::([0-9]{1,18}))?")
ADDON_ORIGINS = {"null", "music-application://desktop", "http://localhost:2007"}


def read_token():
    try:
        token = TOKEN_FILE.read_text().strip()
    except OSError as exc:
        raise RuntimeError("Сначала установи аддон через ./setup-pulsesync") from exc
    if not TOKEN_PATTERN.fullmatch(token):
        raise RuntimeError("Некорректный ключ Pet. Повтори ./setup-pulsesync")
    return token


def create_token():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=TOKEN_FILE.parent, prefix=".pulsesync-token.")
    try:
        with os.fdopen(descriptor, "w") as key_file:
            key_file.write(secrets.token_hex(32) + "\n")
        try:
            os.link(temporary, TOKEN_FILE)
        except FileExistsError:
            pass
    finally:
        Path(temporary).unlink(missing_ok=True)
    return read_token()


class RequestServer(http.server.HTTPServer):
    timeout = 0.25

    def __init__(self, token, command):
        self.token = token
        self.command = {**command, "requestId": secrets.token_hex(16)}
        self.delivered = False
        self.result = None
        super().__init__(("127.0.0.1", PORT), BridgeRequest)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(0.8)
        return connection, address


class BridgeRequest(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, value):
        data = b"" if status == 204 else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin in ADDON_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if status == 204:
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        if (self.path in {"/poll", "/result"} and self.headers.get("Origin") in ADDON_ORIGINS
                and self.headers.get("Access-Control-Request-Method") == "POST"):
            self.respond(204, {})
        else:
            self.respond(403, {})

    def do_POST(self):
        authorization = self.headers.get("Authorization", "")
        origin = self.headers.get("Origin")
        if (origin is not None and origin not in ADDON_ORIGINS or
                self.headers.get("Host") != f"127.0.0.1:{PORT}" or
                not hmac.compare_digest(authorization.encode(), f"Bearer {self.server.token}".encode())):
            self.respond(403, {})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, OSError):
            self.respond(400, {})
            return
        if self.path == "/poll":
            if self.server.delivered:
                self.respond(200, {})
            else:
                self.server.delivered = True
                self.respond(200, self.server.command)
        elif self.path == "/result" and self.server.delivered and data.get("requestId") == self.server.command["requestId"]:
            self.server.result = data
            self.respond(200, {})
        else:
            self.respond(404, {})


def play(command):
    try:
        server = RequestServer(read_token(), command)
    except OSError as exc:
        raise RuntimeError("Локальный порт аддона занят") from exc
    with server:
        deadline = time.monotonic() + 15
        while server.result is None and time.monotonic() < deadline:
            server.handle_request()
        if server.result is None:
            raise RuntimeError("Плеер не ответил" if server.delivered else "Аддон PulseSync не подключился")
        if server.result.get("ok") is not True:
            raise RuntimeError(str(server.result.get("error") or "Не удалось запустить воспроизведение"))
        return server.result


def main():
    parser = argparse.ArgumentParser(description="Управление Яндекс Музыкой через PulseSync")
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--yandex-track", metavar="TRACK[:ALBUM]")
    options.add_argument("--yandex-search", metavar="ТЕКСТ")
    options.add_argument("--yandex-artist", metavar="ИМЯ")
    options.add_argument("--yandex-favorites", action="store_true")
    options.add_argument("--yandex-wave", action="store_true")
    options.add_argument("--create-token", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--alternative", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--artist", metavar="ИМЯ", help="исполнитель для --yandex-search")
    args = parser.parse_args()
    if args.artist is not None and args.yandex_search is None:
        parser.error("--artist используется с --yandex-search")
    try:
        if args.create_token:
            print(create_token())
            return
        if args.yandex_track:
            match = TRACK_PATTERN.fullmatch(args.yandex_track)
            if not match:
                parser.error("Укажи ID трека в формате TRACK или TRACK:ALBUM")
            track, album = match.groups()
            command = {"action": "track", "trackId": track, "albumId": album}
        elif args.yandex_search is not None or args.yandex_artist is not None:
            searching_track = args.yandex_search is not None
            query = " ".join((args.yandex_search if searching_track else args.yandex_artist).split())
            if not 1 <= len(query) <= 200:
                parser.error("Поиск должен содержать от 1 до 200 символов")
            alternatives = [" ".join(value.split()) for value in args.alternative[:2]]
            command = {"action": "search" if searching_track else "artist", "query": query,
                       "alternatives": [value for value in alternatives if 1 <= len(value) <= 200]}
            if args.artist is not None:
                artist = " ".join(args.artist.split())
                if not 1 <= len(artist) <= 200:
                    parser.error("Укажи имя исполнителя")
                command["artist"] = artist
        elif args.yandex_favorites:
            command = {"action": "favorites"}
        else:
            command = {"action": "wave"}
        result = play(command)
        title = result.get("title")
        context = result.get("playlist") or result.get("artist")
        status = f"Играет «{title}»" if title else "Воспроизведение подтверждено"
        print(f"{status} · {context}" if context else status)
    except RuntimeError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
