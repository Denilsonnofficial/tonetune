"""Lógica de acompanhamento do teclado: botões, transições e acorde.

Nada aqui toca som. O motor de áudio chama `advance()` a cada bloco e recebe
os trechos de cada seção que devem soar, o que permite testar as transições
sem placa de som.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ac7 import AC7_PPQ, Rhythm, Section

INTROS = (1, 7)
VARIATIONS = (2, 3, 8, 9)
FILLS = {2: 4, 3: 5, 8: 10, 9: 11}  # variação -> virada correspondente
ENDINGS = (6, 12)

NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

# Os padrões do AC7 são gravados em Dó maior. Cada qualidade diz quanto mover
# as classes de altura (relativas à tônica) para formar o acorde pedido.
CHORD_QUALITIES = {
    "": {},
    "m": {4: -1, 9: -1, 11: -1},
    "7": {11: -1},
    "m7": {4: -1, 9: -1, 11: -1},
    "maj7": {},
    "sus4": {4: 1},
    "dim": {4: -1, 7: -1, 9: -1, 11: -1},
    "aug": {7: 1},
}
MINOR_QUALITIES = ("m", "m7", "dim")


def kind_of(index: int) -> str:
    if index in INTROS:
        return "intro"
    if index in VARIATIONS:
        return "variation"
    if index in ENDINGS:
        return "ending"
    return "fill"


@dataclass(frozen=True)
class Chord:
    root: int = 0
    quality: str = ""

    @property
    def name(self) -> str:
        return f"{NOTE_NAMES[self.root]}{self.quality}"

    @property
    def minor(self) -> bool:
        return self.quality in MINOR_QUALITIES

    def apply(self, pitch: int) -> int:
        """Leva uma nota escrita em Dó para este acorde, no registro próximo."""
        shift = self.root if self.root <= 6 else self.root - 12
        moved = pitch + CHORD_QUALITIES[self.quality].get(pitch % 12, 0)
        return max(0, min(127, moved + shift))


@dataclass(frozen=True)
class Segment:
    """Trecho [start, end) de uma seção a tocar neste bloco."""
    section: Section
    start: float
    end: float


class Arranger:
    def __init__(self, rhythm: Rhythm) -> None:
        self.rhythm = rhythm
        self.current: Section | None = None
        self.tick = 0.0
        self.repeat = False
        self.base: int | None = self._first_variation()
        self._after: int | None = None
        self._switch: tuple[int, float, float] | None = None

    def _first_variation(self) -> int | None:
        for index in VARIATIONS:
            if self.playable(index):
                return index
        return None

    def playable(self, index: int | None) -> bool:
        if index is None:
            return False
        section = self.rhythm.section(index)
        return section is not None and not section.is_empty

    @property
    def next_index(self) -> int | None:
        """A seção que vem depois da atual, para acender no painel."""
        if self.current is None:
            return None
        if self._switch is not None:
            return self._switch[0]
        if self.repeat:
            return None
        if kind_of(self.current.index) in ("intro", "fill"):
            return self._after or self.base
        return None

    def start(self, index: int) -> bool:
        if not self.playable(index):
            return False
        self._switch = None
        self._after = None
        self._enter(index, 0.0)
        return True

    def stop(self) -> None:
        self.current = None
        self._switch = None
        self._after = None
        self.tick = 0.0

    def press(self, index: int) -> bool:
        """Equivale a apertar o botão da seção com o ritmo tocando."""
        if not self.playable(index):
            return False
        if self.current is None:
            return self.start(index)
        if self.repeat:
            self._switch = (index, self.tick, 0.0)
            return True

        current = self.current.index
        kind = kind_of(current)
        target = kind_of(index)
        if target == "variation":
            if kind == "variation" and index == current:
                return self._fill(FILLS[index])
            if kind in ("intro", "fill"):
                self._after = index
                return True
            self._switch_next_bar(index)
            return True
        if target == "fill":
            return kind == "variation" and self._fill(index)
        if index != current:
            self._switch_next_bar(index)
            return True
        return False

    def _next_bar(self) -> float:
        section = self.current
        bar = section.bar_ticks
        return min(float(section.total_ticks),
                   (math.floor(self.tick / bar) + 1) * bar)

    def _switch_next_bar(self, index: int) -> None:
        self._switch = (index, self._next_bar(), 0.0)

    def _fill(self, index: int) -> bool:
        """A virada entra no próximo tempo, na mesma posição do compasso."""
        fill = self.rhythm.section(index)
        if not self.playable(index):
            return False
        section = self.current
        beat = max(1, AC7_PPQ * 4 // section.denominator)
        at = math.ceil(self.tick / beat) * beat
        offset = at % section.bar_ticks
        if at >= section.total_ticks or offset == 0 or offset >= fill.total_ticks:
            at, offset = self._next_bar(), 0
        self._after = section.index
        self._switch = (index, float(at), float(offset))
        return True

    def _enter(self, index: int, tick: float) -> None:
        self.current = self.rhythm.section(index)
        self.tick = tick
        if kind_of(index) == "variation":
            self.base = index
            self._after = None

    def _boundary(self) -> None:
        section = self.current
        if self._switch is not None and self.tick >= self._switch[1] - 1e-6:
            index, _at, offset = self._switch
            self._switch = None
            self._enter(index, offset)
        elif self.repeat or kind_of(section.index) == "variation":
            self.tick = 0.0
        elif kind_of(section.index) == "ending":
            self.stop()
        else:
            following = self._after or self.base
            if self.playable(following):
                self._enter(following, 0.0)
            else:
                self.stop()

    def advance(self, ticks: float) -> list[Segment]:
        segments: list[Segment] = []
        while ticks > 1e-9 and self.current is not None:
            section = self.current
            limit = float(section.total_ticks)
            if self._switch is not None:
                limit = min(limit, self._switch[1])
            step = min(ticks, limit - self.tick)
            if step > 0:
                segments.append(Segment(section, self.tick, self.tick + step))
                self.tick += step
                ticks -= step
            if self.tick >= limit - 1e-6:
                self._boundary()
        return segments
