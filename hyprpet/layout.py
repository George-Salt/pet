import math
from functools import cache
from itertools import combinations

from .appearance import BACKGROUNDS, PALETTE

PET_RADIUS = 28
BUTTON_RADIUS = 18
PADDING = 8
ACTIONS = {
    "previous": "Предыдущий трек",
    "play-pause": "Пауза / продолжить",
    "next": "Следующий трек",
    "quieter": "Тише",
    "louder": "Громче",
    "voice": "Ключевое слово «Пэт»",
    "yandex-wave": "Моя волна",
    "yandex-search": "Поиск музыки",
    "yandex-artist": "Песни исполнителя",
    "yandex-favorites": "Мне нравится",
    "settings": "Настройки",
    "fullscreen": "Поверх полноэкранных окон",
    "quit": "Закрыть Pet",
    "back": "Назад",
    "appearance": "Внешний вид",
    "accent": "Акцентный цвет",
    "border": "Обводка",
    **BACKGROUNDS,
    **{action: name for action, (name, _) in PALETTE.items()},
}
CONTROLS = ("previous", "play-pause", "next", "quieter", "louder", "yandex-wave", "voice", "settings")
SETTINGS = ("fullscreen", "appearance", "quit", "back")
STYLE = (*BACKGROUNDS, "border", "accent", "back")
COLORS = (*PALETTE, "back")


def clamp(value, minimum, maximum):
    if maximum < minimum:
        return (minimum + maximum) / 2
    return min(maximum, max(minimum, value))


def pet_position(x, y, width, height):
    margin = PET_RADIUS + PADDING
    return clamp(x, margin, width - margin), clamp(y, margin, height - margin)


@cache
def arrangements(count):
    def arc(count, span, radius, vary=True):
        return [
            (math.radians(span * (i / (count - 1) - 0.5)),
             radius + (i % 3 - 1) * (6 if vary else 0))
            for i in range(count)
        ]

    if count >= 5:
        templates = [arc(count, span, radius) for span, radius in
                     ((290, 72), (235, 76), (180, 90), (142, 109))]
        # Two staggered arcs fit the corner without squeezing the hit targets.
        inner = count // 2
        templates.append(arc(inner, 70, 95 if inner >= 4 else 78) +
                         arc(count - inner, 72, 148 if inner >= 4 else 139, False))
    else:
        templates = [arc(count, 154 if count == 4 else 125, 67), arc(count, 91, 86)]
    directions = [-math.pi / 2 + j * math.tau / 16 for j in range(16)]
    return tuple(
        tuple(tuple((radius * math.cos(heading + angle), radius * math.sin(heading + angle))
                    for angle, radius in template) for heading in directions)
        for template in templates
    )


TARGET_MARGIN = BUTTON_RADIUS + 8


def button_offsets(x, y, width, height, count, previous=None):
    # Start turning the formation before the moving buttons reach the edge.
    inset = TARGET_MARGIN + min(15, min(width, height) / 24)
    left, right = inset - x, width - inset - x
    top, bottom = inset - y, height - inset - y

    def overflow(offsets):
        return sum(abs(dx - clamp(dx, left, right)) + abs(dy - clamp(dy, top, bottom))
                   for dx, dy in offsets)

    def distance(offsets):
        if previous is None:
            return 0
        return sum((a - x0) ** 2 + (b - y0) ** 2 for (a, b), (x0, y0) in zip(offsets, previous))

    tiers = arrangements(count)
    previous_tier = next((i for i, tier in enumerate(tiers) if previous in tier), 0)
    fallback_score = (math.inf, math.inf)
    fallback = None
    for i, tier in enumerate(tiers):
        # Prefer the widest formation that fits; proximity only chooses its angle.
        # A little extra clearance when widening prevents oscillation at the seam.
        if i == previous_tier and previous is not None and overflow(previous) == 0:
            return previous
        fits = []
        clearance = 4 if i < previous_tier else 0
        for offsets in tier:
            excess = overflow(offsets)
            if excess == 0 and (not clearance or all(left + clearance <= dx <= right - clearance
                                                     and top + clearance <= dy <= bottom - clearance
                                                     for dx, dy in offsets)):
                fits.append(offsets)
            score = excess, distance(offsets)
            if score < fallback_score:
                fallback_score, fallback = score, offsets
        if fits:
            return min(fits, key=distance)
    return fallback


def follow_targets(x, y, width, height, offsets, targets, movement, elapsed):
    margin = TARGET_MARGIN
    destination = [(clamp(x + dx, margin, width - margin), clamp(y + dy, margin, height - margin))
                   for dx, dy in offsets]
    if not targets:
        return destination
    dx, dy = movement
    # The group follows the pet until a button reaches the inset. Keep their
    # spacing during that translation, then ease into the new arrangement.
    dx = clamp(0.86 * dx, margin - min(tx for tx, _ in targets), width - margin - max(tx for tx, _ in targets))
    dy = clamp(0.86 * dy, margin - min(ty for _, ty in targets), height - margin - max(ty for _, ty in targets))
    blend = -math.expm1(-11 * elapsed)
    moved = [(tx + dx, ty + dy) for tx, ty in targets]
    next_targets = [(tx + (end_x - tx) * blend, ty + (end_y - ty) * blend)
                    for (tx, ty), (end_x, end_y) in zip(moved, destination)]
    separated = separate_targets(x, y, width, height, next_targets, destination)

    result = []
    for (tx, ty), (end_x, end_y) in zip(moved, separated):
        vx, vy = end_x - tx, end_y - ty
        fraction = min(1, (640 * elapsed) / max(math.hypot(vx, vy), 1))
        result.append((tx + vx * fraction, ty + vy * fraction))
    return result


def separate_targets(x, y, width, height, targets, destination):
    # Keep the buttons readable while two valid arrangements pass through
    # intermediate positions that may otherwise overlap.
    margin = TARGET_MARGIN
    button_gap = 2 * BUTTON_RADIUS + 6
    pet_gap = PET_RADIUS + BUTTON_RADIUS + 5
    pairs = tuple(combinations(range(len(targets)), 2))

    def nudge(index, dx, dy):
        tx, ty = targets[index]
        targets[index] = (clamp(tx + dx, margin, width - margin),
                          clamp(ty + dy, margin, height - margin))

    for _ in range(14):
        adjusted = False
        for i, (tx, ty) in enumerate(targets):
            dx, dy = tx - x, ty - y
            distance = math.hypot(dx, dy)
            if distance + 0.15 < pet_gap:
                if distance < 0.01:
                    dx, dy = destination[i][0] - x, destination[i][1] - y
                scale = (pet_gap - distance) / max(math.hypot(dx, dy), 1)
                nudge(i, dx * scale, dy * scale)
                adjusted = True
        for i, j in pairs:
            dx, dy = targets[i][0] - targets[j][0], targets[i][1] - targets[j][1]
            distance = math.hypot(dx, dy)
            if distance + 0.15 < button_gap:
                if distance < 0.01:
                    dx, dy = destination[i][0] - destination[j][0], destination[i][1] - destination[j][1]
                scale = 0.5 * (button_gap - distance) / max(math.hypot(dx, dy), 1)
                nudge(i, dx * scale, dy * scale)
                nudge(j, -dx * scale, -dy * scale)
                adjusted = True
        if not adjusted:
            break
    return targets


def button_frames(x, y, width, height, progress, seconds, actions, targets):
    for i, (action, (tx, ty)) in enumerate(zip(actions, targets)):
        # Each button leaves the pet at its own angle, then curves into place.
        delay = (i % 4) * 0.044 + (i // 4) * 0.028
        amount = clamp((progress - delay) / (1 - delay), 0, 1)
        travel = amount * amount * (3 - 2 * amount)
        distance = math.hypot(tx - x, ty - y)
        heading = math.atan2(ty - y, tx - x)
        spin = (1 if i % 2 else -1) * (0.32 + i % 3 * 0.11)
        phase = seconds * (1.45 + i % 4 * 0.14) + i * 0.72
        drift = math.sin(phase * 0.82) * 4.2
        angle = heading + spin * (1 - travel) + drift * travel / max(distance, 1)
        radius = distance * travel
        radius += math.cos(phase) * (3.4 + i % 3 * 0.45) * travel
        yield (action, clamp(x + math.cos(angle) * radius, BUTTON_RADIUS + 2, width - BUTTON_RADIUS - 2),
               clamp(y + math.sin(angle) * radius, BUTTON_RADIUS + 2, height - BUTTON_RADIUS - 2),
               BUTTON_RADIUS * (0.58 + 0.42 * travel), travel)
