from dataclasses import dataclass

BACKGROUNDS = {"background-solid": "Чёрный фон", "background-blur": "Размытие фона"}
PALETTE = {
    "accent-white": ("Белый", (1.0, 1.0, 1.0)),
    "accent-blue": ("Голубой", (0.58, 0.78, 1.0)),
    "accent-lilac": ("Сиреневый", (0.78, 0.66, 1.0)),
    "accent-mint": ("Мятный", (0.55, 0.92, 0.77)),
    "accent-amber": ("Янтарный", (1.0, 0.80, 0.50)),
}


@dataclass(frozen=True)
class Appearance:
    background: str = "background-solid"
    border: bool = True
    accent: str = "accent-white"

    @property
    def translucent(self):
        return self.background == "background-blur"

    @property
    def color(self):
        return PALETTE[self.accent][1]
