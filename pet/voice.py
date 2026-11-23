"""Stream Russian wake-word and command events from the PipeWire microphone."""
import json
import os
from collections import deque
from pathlib import Path
import re
import signal
import subprocess
import sys

from .audio import capture_args

DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "hyprpet"
MODEL = DATA / "vosk-model-small-ru-0.22"
ENGLISH_MODEL = DATA / "vosk-model-small-en-us-0.15"
PYTHON = DATA / "voice/bin/python"

WAKE_WORDS = ("пэт", "пет")
SEARCH_VERBS = ("включи", "поставь")
SEARCH_ALIASES = dict.fromkeys(("зависть", "jealous", "джелос", "джелас", "джэлас"), "yandex-jealous")
ARTIST_PREFIXES = (
    "песни исполнителя", "треки исполнителя", "песни артиста", "треки артиста",
    "песни исполнители", "треки исполнители", "песни артисты", "треки артисты",
    "исполнителя", "артиста", "песни", "треки",
)
PERFORMER_SEPARATOR = re.compile(r"\s+(?:исполнитель|исполнителя|исполнительница|исполнительницы|в исполнении|от|by)\s+", re.I)
PHRASES = {
    "next": ("вперёд", "дальше", "следующий", "следующая", "следующий трек",
             "следующая песня", "переключи трек", "пропусти"),
    "previous": ("назад", "предыдущий", "предыдущая", "предыдущий трек", "предыдущая песня"),
    "louder": ("громче", "погромче", "громкость выше", "прибавь", "прибавь громкость"),
    "quieter": ("тише", "потише", "громкость ниже", "убавь", "убавь громкость"),
    "pause": ("пауза", "на паузу", "стоп", "останови"),
    "play": ("продолжи", "продолжить", "играй", "воспроизведение"),
    "yandex-wave": ("моя волна", "включи мою волну", "верни мою волну", "верни волну"),
    "yandex-favorites": (
        "мне нравится", "включи мне нравится", "включи плейлист мне нравится",
        "поставь плейлист мне нравится", "включи любимые треки",
    ),
}
LOOKUP = {phrase.replace("ё", "е"): action for action, phrases in PHRASES.items() for phrase in phrases}
RATE = 16000
FOLLOW_UP = 4
MIN_COMMAND_CONFIDENCE = 0.70


class Commands:
    def __init__(self, emit):
        self.emit = emit
        self.armed_until = 0
        self.heard_wake = False

    def accept(self, text, seconds, confidence=None):
        words = text.replace("ё", "е").split()
        wake = bool(words and words[0] in WAKE_WORDS)
        if wake and not self.heard_wake:
            self.heard_wake = True
            self.emit("wake")
        if confidence is None:
            return

        phrase = " ".join(words[1:] if wake else words)
        allowed = wake or seconds < self.armed_until
        wake_heard = self.heard_wake
        self.heard_wake = False
        command_confidence = confidence[1:] if wake else confidence
        action = LOOKUP.get(phrase) if allowed else None
        if action and command_confidence and min(command_confidence) >= MIN_COMMAND_CONFIDENCE:
            self.emit("command", action)
            self.armed_until = 0
            return None
        if wake:
            self.armed_until = seconds + FOLLOW_UP
        if (allowed and phrase) or (wake_heard and words and words[0] in SEARCH_VERBS):
            return wake or wake_heard
        return None

    def finish_search(self, request):
        if request:
            verb, query, alternatives = request
            prefix = artist_prefix(query)
            if prefix:
                self.emit("artist", query[len(prefix):].strip(), alternatives=alternatives)
            else:
                choices = (query, *alternatives)
                known = LOOKUP.get(f"{verb} {query}".replace("ё", "е"))
                if known is None and not any(PERFORMER_SEPARATOR.search(value) for value in choices):
                    known = next((SEARCH_ALIASES[value] for value in choices if value in SEARCH_ALIASES), None)
                if known:
                    self.emit("command", known)
                else:
                    self.emit("search", query, alternatives=alternatives)
            self.armed_until = 0


def emit(kind, action=None, **details):
    print(json.dumps({"event": kind, "action": action, **details}), flush=True)


def music_request(text, with_wake):
    words = text.split()
    # The unrestricted model may mishear the pet's name. The separate wake
    # recognizer authorizes ignoring up to two words before the playback verb.
    for i, word in enumerate(words[:3 if with_wake else 1]):
        if word in SEARCH_VERBS:
            return query_after(word, words[i + 1:])
    return None


def query_after(verb, words):
    if len(words) > 1 and words[0] in ("трек", "песню"):
        words = words[1:]
    query = " ".join(words)
    return (verb, query) if 0 < len(query) <= 200 and "[unk]" not in query else None


def artist_prefix(query):
    return next((prefix for prefix in ARTIST_PREFIXES if query.startswith(prefix + " ")), "")


class SearchUtterance:
    def __init__(self, new_dictation, new_english, seconds, samples, words, with_wake):
        self.new_dictation = new_dictation
        self.new_english = new_english
        self.recognizer = new_dictation()
        self.samples = bytearray()
        self.deadline = seconds + 3.5
        self.start = seconds - len(samples) / (2 * RATE)
        self.with_wake = with_wake
        first = 1 if words and words[0]["word"] in WAKE_WORDS else 0
        self.trigger = words[first] if first < len(words) else None
        self.wake_end = words[0]["end"] - self.start if first else 0

    def decode_tail(self, factory, start, end=None):
        offset = max(0, round((start - 0.055) * RATE)) * 2
        stop = len(self.samples) if end is None else min(len(self.samples), round(end * RATE) * 2)
        if offset >= stop:
            return {"text": ""}
        recognizer = factory()
        samples = bytes(self.samples[offset:stop])
        result = recognizer.Result() if recognizer.AcceptWaveform(samples) else recognizer.FinalResult()
        return json.loads(result)

    def feed(self, samples, seconds, final=False):
        self.samples.extend(samples)
        if samples and self.recognizer.AcceptWaveform(samples):
            return json.loads(self.recognizer.Result())
        if final or seconds >= self.deadline:
            return json.loads(self.recognizer.FinalResult())
        return None

    def request(self, decoded):
        russian = music_request(decoded["text"], self.with_wake)
        trigger = next((word for word in decoded.get("result", [])[:3 if self.with_wake else 1]
                        if word["word"] in SEARCH_VERBS), None)
        start = trigger["end"] if trigger else None
        if start is None and self.trigger and self.trigger["word"] in SEARCH_VERBS and self.trigger["conf"] >= 0.65:
            trigger = self.trigger
            start = trigger["end"] - self.start
        if russian is None and start is not None:
            russian = query_after(trigger["word"], self.decode_tail(self.new_dictation, start)["text"].split())

        english = ""
        if self.new_english:
            english = self.english_query(decoded.get("result", []), start, russian)
        if russian:
            verb, query = russian
            return verb, query, [english] if english and english != query else []
        return ("play", english, []) if english else None

    def english_query(self, words, start, russian):
        if start is not None and russian:
            tokens = [word for word in words if word["start"] >= start - 0.035]
            actor = artist_prefix(russian[1])
            if actor and len(tokens) > len(actor.split()):
                return english_words(self.decode_tail(self.new_english, tokens[len(actor.split()) - 1]["end"]))
            for i, word in enumerate(tokens[1:-1], start=1):
                end = i
                if word["word"] == "в" and tokens[i + 1]["word"] == "исполнении" and i + 2 < len(tokens):
                    end += 1
                elif word["word"] not in ("исполнитель", "исполнителя", "исполнительница", "от"):
                    continue
                title = english_words(self.decode_tail(self.new_english, start, word["start"]))
                name = english_words(self.decode_tail(self.new_english, tokens[end]["end"]))
                return f"{title} исполнитель {name}" if title and name else ""
        waveform = self.wake_end if start is None else start
        return english_words(self.decode_tail(self.new_english, waveform), require_play=start is None)


def english_words(take, require_play=False):
    words = take["text"].split()
    if words[:1] == ["play"]:
        words = words[1:]
    elif require_play:
        return ""
    confidence = [word["conf"] for word in take.get("result", []) if word["word"] != "play"]
    return " ".join(words) if confidence and sum(confidence) / len(confidence) >= 0.52 else ""


def begin_search(commands, decoded, seconds, samples, new_dictation, new_english, final=False):
    words = decoded.get("result", [])
    with_wake = commands.accept(decoded["text"], seconds, [word["conf"] for word in words])
    if with_wake is None or not samples:
        return None
    search = SearchUtterance(new_dictation, new_english, seconds, samples, words, with_wake)
    text = search.feed(samples, seconds, final=final)
    if text is None:
        return search
    commands.finish_search(search.request(text))
    return None


def consume(recognizer, pcm, new_dictation, new_english=None):
    commands = Commands(emit)
    audio = deque(maxlen=160)
    pending = None
    byte_count = 0
    while data := pcm.read(1600):
        byte_count += len(data)
        seconds = byte_count / (2 * RATE)
        ended = recognizer.AcceptWaveform(data)
        if pending:
            if ended:
                recognizer.Result()
            text = pending.feed(data, seconds)
            if text is not None:
                commands.finish_search(pending.request(text))
                pending = None
                recognizer.Reset()
            continue
        audio.append(data)
        if ended:
            pending = begin_search(commands, json.loads(recognizer.Result()), seconds, b"".join(audio),
                                   new_dictation, new_english)
            audio.clear()
        else:
            commands.accept(json.loads(recognizer.PartialResult())["partial"], seconds)
    seconds = byte_count / (2 * RATE)
    if pending:
        commands.finish_search(pending.request(pending.feed(b"", seconds, final=True)))
    else:
        begin_search(commands, json.loads(recognizer.FinalResult()), seconds, b"".join(audio),
                     new_dictation, new_english, final=True)


def main():
    from vosk import KaldiRecognizer, Model, SetLogLevel

    def stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    SetLogLevel(-1)
    model = Model(str(MODEL))
    aliases = [phrase for phrases in PHRASES.values() for phrase in phrases
               if all(model.vosk_model_find_word(word) >= 0 for word in phrase.split())]
    prefixes = [*SEARCH_VERBS, *(f"{verb} [unk]" for verb in SEARCH_VERBS)]
    prefixes.extend(f"{verb} {pattern}" for verb in SEARCH_VERBS for pattern in (
        "[unk] исполнитель [unk]", "песни артиста [unk]", "треки исполнителя [unk]",
    ))
    grammar = [*WAKE_WORDS, *aliases, *prefixes, "[unk]"]
    grammar.extend(f"{wake} {phrase}" for wake in WAKE_WORDS for phrase in (*aliases, *prefixes, "[unk]"))
    recognizer = KaldiRecognizer(model, RATE, json.dumps(grammar, ensure_ascii=False))
    recognizer.SetWords(True)
    english_model = None

    def dictation(language_model):
        decoder = KaldiRecognizer(language_model, RATE)
        decoder.SetWords(True)
        return decoder

    def new_english():
        nonlocal english_model
        if english_model is None:
            english_model = Model(str(ENGLISH_MODEL))
        return dictation(english_model)

    with subprocess.Popen(
        capture_args(RATE),
        stdout=subprocess.PIPE,
    ) as mic:
        try:
            emit("ready")
            consume(recognizer, mic.stdout, lambda: dictation(model),
                    new_english if (ENGLISH_MODEL / "am/final.mdl").is_file() else None)
            if mic.wait() != 0:
                raise RuntimeError("Не удалось открыть микрофон")
        finally:
            if mic.poll() is None:
                mic.terminate()
                try:
                    mic.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    mic.kill()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ImportError) as exc:
        sys.exit(str(exc))
