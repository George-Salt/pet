"""Select a Linux audio backend for Pet."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def capture_args(rate):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    pipewire = shutil.which("pw-record")
    pulse = shutil.which("parec")
    remote = os.environ.get("PIPEWIRE_REMOTE", "pipewire-0")
    if pipewire and ((runtime / remote).is_socket() or not pulse):
        return [
            pipewire, "--raw", "--format", "s16", "--rate", str(rate), "--channels", "1",
            "--latency", "50ms", "--properties", "application.name=Pet", "-",
        ]
    if pulse:
        return [pulse, "--raw", "--format=s16le", f"--rate={rate}", "--channels=1", "--client-name=Pet"]
    raise RuntimeError("Установи PipeWire или PulseAudio для микрофона")


def change_volume(action):
    sink = "@DEFAULT_AUDIO_SINK@"
    wpctl = shutil.which("wpctl")
    if wpctl:
        amount = "5%+" if action == "louder" else "5%-"
        command = [wpctl, "set-volume", "-l", "1.0", sink, amount]
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
    pulse = shutil.which("pactl")
    if not pulse:
        raise RuntimeError("Громкость недоступна")
    sink = "@DEFAULT_SINK@"
    delta = 5 if action == "louder" else -5
    env = dict(os.environ, LC_ALL="C")
    current = subprocess.check_output([pulse, "get-sink-volume", sink], env=env, stderr=subprocess.DEVNULL)
    volumes = re.findall(rb"(\d+)%", current.split(b"\n", 1)[0])
    if not volumes:
        raise RuntimeError("Громкость недоступна")
    adjusted = [f"{max(0, min(100, int(value) + delta))}%" for value in volumes]
    subprocess.run([pulse, "set-sink-volume", sink, *adjusted], check=True)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ("louder", "quieter"):
            raise RuntimeError("Укажи louder или quieter")
        change_volume(sys.argv[1])
    except (RuntimeError, OSError, subprocess.CalledProcessError):
        sys.exit("Громкость недоступна")
