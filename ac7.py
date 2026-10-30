"""Leitor de ritmos AC7 do Casio CT-X800 (e família CT-X).

O formato é o mesmo usado pelo teclado e pelo editor ctx800-editor. O leitor
extrai nome, andamento, compasso, seções (elementos) e as notas de cada parte,
sem modificar o arquivo. `export_ac7` grava uma cópia com a mixagem alterada.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

AC7_MAGIC = b"AC07"
AC7_PPQ = 96  # ticks AC7 por semínima

SECTION_NAMES = {
    1: "Intro 1",
    2: "Variação 1",
    3: "Variação 2",
    4: "Fill 1",
    5: "Fill 2",
    6: "Ending 1",
    7: "Intro 2",
    8: "Variação 3",
    9: "Variação 4",
    10: "Fill 3",
    11: "Fill 4",
    12: "Ending 2",
}

PART_NAMES = {
    1: "Percussão",
    2: "Bateria",
    3: "Baixo",
    4: "Piano",
    5: "Strings",
    6: "E. Piano",
    7: "Violão",
    8: "Pad/Coro",
}


class AC7Error(Exception):
    """Arquivo AC7 inválido ou incompleto."""


@dataclass(frozen=True)
class Mixer:
    patch: int
    bank: int
    volume: int
    pan: int
    reverb: int
    chorus: int

    def to_dict(self) -> dict:
        return {
            "patch": self.patch,
            "bank": self.bank,
            "volume": self.volume,
            "pan": self.pan,
            "reverb": self.reverb,
            "chorus": self.chorus,
        }


DEFAULT_MIXERS = {
    1: Mixer(0, 120, 100, 64, 40, 0),
    2: Mixer(0, 120, 110, 64, 40, 0),
    3: Mixer(33, 0, 100, 64, 30, 0),
    4: Mixer(0, 0, 90, 64, 50, 0),
    5: Mixer(49, 0, 100, 64, 50, 0),
    6: Mixer(4, 0, 90, 64, 40, 0),
    7: Mixer(25, 0, 85, 64, 40, 0),
    8: Mixer(61, 0, 80, 64, 50, 0),
}


@dataclass
class Note:
    tick: int
    pitch: int
    velocity: int
    length: int = 0


@dataclass
class Track:
    part: int
    notes: list[Note] = field(default_factory=list)
    raw_part: int = 0
    max_tick: int = 0

    @property
    def chord_sync(self) -> bool:
        return not bool(self.raw_part & 0x10)

    @property
    def major_only(self) -> bool:
        return self.raw_part & 0xA0 == 0x80

    @property
    def minor_only(self) -> bool:
        return self.raw_part & 0xA0 == 0xA0


@dataclass
class Section:
    index: int
    measures: int
    beats: int
    denominator: int
    total_ticks: int
    tracks: list[Track] = field(default_factory=list)
    mixers: dict[int, Mixer] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return SECTION_NAMES.get(self.index, f"Elemento {self.index}")

    @property
    def short_name(self) -> str:
        return self.name.replace("Variação", "Var.").replace("Intro", "Intro")

    @property
    def signature(self) -> str:
        return f"{self.beats}/{self.denominator}"

    @property
    def bar_ticks(self) -> int:
        return max(1, self.beats * 4 * AC7_PPQ // self.denominator)

    @property
    def beats_per_bar(self) -> int:
        return self.bar_ticks // AC7_PPQ

    @property
    def note_count(self) -> int:
        return sum(len(track.notes) for track in self.tracks)

    @property
    def is_empty(self) -> bool:
        return self.note_count == 0

    def notes_for_part(self, part: int) -> list[Note]:
        notes: list[Note] = []
        for track in self.tracks:
            if track.part == part:
                notes.extend(track.notes)
        return notes

    def note_count_for_part(self, part: int) -> int:
        return sum(len(track.notes) for track in self.tracks
                   if track.part == part)

    def mixer_for_part(self, part: int) -> Mixer:
        return self.mixers.get(part, DEFAULT_MIXERS.get(part, DEFAULT_MIXERS[1]))

    def active_parts(self) -> list[int]:
        return [part for part in range(1, 9) if self.note_count_for_part(part)]


@dataclass
class Rhythm:
    path: Path
    name: str
    tempo: int
    beats: int
    denominator: int
    sections: list[Section]
    mixers: dict[int, Mixer] = field(default_factory=dict)
    file_size: int = 0
    tempo_offset: int = -1
    mixer_refs: list[tuple[int, int]] = field(default_factory=list)  # parte, byte

    @property
    def display_name(self) -> str:
        clean = self.name.strip()
        return clean or self.path.stem

    @property
    def signature(self) -> str:
        return f"{self.beats}/{self.denominator}"

    def section(self, index: int) -> Section | None:
        for section in self.sections:
            if section.index == index:
                return section
        return None

    def first_playable(self) -> Section | None:
        for section in self.sections:
            if not section.is_empty:
                return section
        return self.sections[0] if self.sections else None

    def mixer_for_part(self, part: int) -> Mixer:
        for section in self.sections:
            if part in section.mixers:
                return section.mixers[part]
        return self.mixers.get(part, DEFAULT_MIXERS.get(part, DEFAULT_MIXERS[1]))

    def duration(self, section: Section, tempo: int | None = None) -> float:
        bpm = max(20, min(300, tempo or self.tempo))
        return section.total_ticks / AC7_PPQ * 60.0 / bpm


# ----------------------------------------------------------------- helpers

def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _atoms(data: bytes, start: int, end: int):
    """Itera (tipo, payload) até o átomo FF ou o fim da área."""
    pos = start
    while pos + 1 < end:
        kind = data[pos]
        length = data[pos + 1]
        pos += 2
        if pos + length > end:
            break
        yield kind, data[pos:pos + length]
        pos += length
        if kind == 0xFF:
            break


def _timesig(payload: bytes, fallback=(4, 4)) -> tuple[int, int]:
    if not payload:
        return fallback
    value = payload[0]
    numerator = value >> 3
    denominator = 1 << (value & 0x07)
    if not 1 <= numerator <= 16 or not 1 <= denominator <= 16:
        return fallback
    return numerator, denominator


def _segment_table(data: bytes, offset: int) -> tuple[list[int], int]:
    if offset + 10 > len(data):
        raise AC7Error("Segmento de faixas fora do arquivo.")
    count = _u16(data, offset + 8)
    addresses = []
    for index in range(count):
        pos = offset + 10 + index * 4
        if pos + 4 > len(data):
            raise AC7Error("Tabela de faixas incompleta.")
        addresses.append(_u32(data, pos))
    return addresses, offset + _u32(data, offset + 4)


def _part_from_raw(raw: int) -> int:
    low = raw & 0x0F
    return 1 if low == 0x0F else low + 2


def _parse_track(data: bytes, start: int, end: int, part: int,
                 total_ticks: int, has_starter: bool) -> Track:
    track = Track(part=part)
    pos = start + (3 if has_starter else 0)
    time = 0
    open_notes: dict[int, list[Note]] = {}
    while pos + 2 < end:
        delta, note, velocity = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        if delta:
            time += delta
        if note == 0xFC:  # fim da faixa
            break
        if note == 0xFF:  # salto de tempo
            if delta == 128 and velocity == 4:
                time = max(time, total_ticks)
            else:
                time += 255 * velocity
            track.max_tick = max(track.max_tick, time)
            continue
        if note < 0x80:
            if velocity > 0:
                item = Note(time, note, velocity)
                track.notes.append(item)
                open_notes.setdefault(note, []).append(item)
            else:
                stack = open_notes.get(note)
                if stack:
                    item = stack.pop()
                    item.length = max(1, time - item.tick)
        track.max_tick = max(track.max_tick, time)

    is_drum = part in (1, 2)
    for stack in open_notes.values():
        for item in stack:
            if is_drum:
                # Bateria/percussão normalmente não tem note-off no AC7.
                # Um comprimento curto evita notas "presas" no MIDI e barras
                # gigantes na visualização; o sample do kit já tem cauda.
                item.length = max(1, min(24, total_ticks - item.tick))
            else:
                item.length = max(1, total_ticks - item.tick)
    if is_drum:
        for item in track.notes:
            item.length = max(1, min(item.length, 48))
    return track


def load_ac7(path: str | Path) -> Rhythm:
    """Lê um arquivo .AC7 e devolve o ritmo pronto para reprodução."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AC7Error(f"Não foi possível abrir o arquivo: {exc}") from exc

    if len(data) < 24 or data[:4] != AC7_MAGIC:
        raise AC7Error("Este arquivo não é um ritmo AC7 válido.")
    declared = _u32(data, 4)
    if declared > len(data):
        raise AC7Error("O arquivo AC7 está incompleto.")

    elements_offset, mixr_offset, drum_offset, othr_offset = struct.unpack_from(
        "<4I", data, 8)
    for label, offset in (
        ("elementos", elements_offset),
        ("mixer", mixr_offset),
        ("bateria", drum_offset),
        ("melodias", othr_offset),
    ):
        if not 24 <= offset < len(data):
            raise AC7Error(f"Segmento de {label} inválido no AC7.")

    if _u32(data, elements_offset) != 0x07FFFFFF:
        raise AC7Error("Cabeçalho de elementos inválido.")

    segment_size = _u16(data, elements_offset + 4)
    count = data[elements_offset + 6]
    if count == 0:
        raise AC7Error("O AC7 não contém elementos.")
    count = min(count, 12)

    definition_offsets = []
    for index in range(count):
        pos = elements_offset + 7 + index * 4
        if pos + 4 > len(data):
            raise AC7Error("Lista de elementos incompleta.")
        definition_offsets.append(_u32(data, pos))
    definitions = [elements_offset + value for value in definition_offsets]
    global_end = definitions[0] if definitions else elements_offset + segment_size
    global_end = min(max(global_end, elements_offset), len(data))

    name = ""
    beats, denominator = 4, 4
    tempo = 120
    tempo_offset = -1
    atom_pos = elements_offset + 7 + count * 4
    for kind, payload in _atoms(data, atom_pos, global_end):
        atom_start = atom_pos + 2
        atom_pos = atom_start + len(payload)
        if kind == 0x00:
            name = payload.split(b"\x00")[0].decode("latin-1", "replace")
        elif kind == 0x01:
            beats, denominator = _timesig(payload)
        elif kind == 0x02 and payload:
            tempo = payload[0] or 120
            tempo_offset = atom_start
    tempo = max(20, min(300, tempo))

    if _u32(data, mixr_offset) != int.from_bytes(b"MIXR", "little"):
        raise AC7Error("Segmento MIXR inválido.")
    mixer_count = _u16(data, mixr_offset + 8)
    mixers: list[Mixer] = []
    mixer_offsets: list[int] = []
    for index in range(mixer_count):
        pos = _u32(data, mixr_offset + 10 + index * 4)
        mixer_offsets.append(pos)
        if pos + 6 > len(data):
            mixers.append(DEFAULT_MIXERS[1])
            continue
        mixers.append(Mixer(
            patch=data[pos],
            bank=data[pos + 1],
            volume=data[pos + 2],
            pan=data[pos + 3],
            reverb=data[pos + 4],
            chorus=data[pos + 5],
        ))

    drum_addresses, drum_end = _segment_table(data, drum_offset)
    othr_addresses, othr_end = _segment_table(data, othr_offset)
    drum_sorted = sorted(set(drum_addresses))
    othr_sorted = sorted(set(othr_addresses))

    def track_end(address: int, sorted_addresses: list[int],
                   segment_end: int) -> int:
        for candidate in sorted_addresses:
            if candidate > address:
                return min(candidate, segment_end)
        return segment_end

    sections: list[Section] = []
    mixer_refs: list[tuple[int, int]] = []
    for index, definition in enumerate(definitions, start=1):
        if definition + 6 > len(data) or data[definition:definition + 4] != b"ELMT":
            continue
        length = _u16(data, definition + 4)
        definition_end = min(definition + length, len(data))
        fields = {kind: payload for kind, payload in
                  _atoms(data, definition + 6, definition_end)}

        sec_beats, sec_denominator = beats, denominator
        if 0x01 in fields:
            sec_beats, sec_denominator = _timesig(fields[0x01],
                                                  (beats, denominator))
        measures = fields.get(0x06, b"\x04")[0] or 4
        track_count = fields.get(0x07, b"\x00")[0]
        raw_indices = fields.get(0x20, b"")
        raw_mixers = fields.get(0x21, b"")
        raw_parts = fields.get(0x22, b"")
        track_count = min(track_count, len(raw_parts),
                          len(raw_indices) // 2, len(raw_mixers) // 2)
        bar_ticks = max(1, sec_beats * 4 * AC7_PPQ // sec_denominator)
        total_ticks = measures * bar_ticks

        section = Section(
            index=index,
            measures=measures,
            beats=sec_beats,
            denominator=sec_denominator,
            total_ticks=total_ticks,
        )

        for track_no in range(track_count):
            raw_part = raw_parts[track_no]
            part = _part_from_raw(raw_part)
            if not 1 <= part <= 8:
                continue
            mixer_ref = _u16(raw_mixers, track_no * 2) & 0x7FFF
            if (mixer_ref < len(mixers)
                    and mixer_offsets[mixer_ref] + 6 <= len(data)):
                mixer_refs.append((part, mixer_offsets[mixer_ref]))
            is_drum = part in (1, 2)
            addresses = drum_addresses if is_drum else othr_addresses
            address_list = drum_sorted if is_drum else othr_sorted
            segment_end = drum_end if is_drum else othr_end
            track_index = _u16(raw_indices, track_no * 2) & 0x7FFF
            if track_index >= len(addresses):
                continue
            start = addresses[track_index]
            if not 0 <= start < len(data):
                continue
            end = track_end(start, address_list, segment_end)
            end = min(end, len(data))
            if end <= start + (3 if not is_drum else 0):
                continue
            track = _parse_track(data, start, end, part, total_ticks,
                                 has_starter=not is_drum)
            track.raw_part = raw_part
            section.tracks.append(track)

            mixer_index = _u16(raw_mixers, track_no * 2) & 0x7FFF
            if (mixer_index < len(mixers) and part not in section.mixers
                    and mixer_index != 0x7FFF):
                section.mixers[part] = mixers[mixer_index]

        sections.append(section)

    if not sections:
        raise AC7Error("O AC7 não tem seções legíveis.")

    rhythm_mixers: dict[int, Mixer] = {}
    for section in sections:
        for part, mixer in section.mixers.items():
            rhythm_mixers.setdefault(part, mixer)
    for part in range(1, 9):
        rhythm_mixers.setdefault(part, DEFAULT_MIXERS[part])

    return Rhythm(
        path=path,
        name=name,
        tempo=tempo,
        beats=beats,
        denominator=denominator,
        sections=sections,
        mixers=rhythm_mixers,
        file_size=len(data),
        tempo_offset=tempo_offset,
        mixer_refs=mixer_refs,
    )


def export_ac7(rhythm: Rhythm, target: str | Path, tempo: int,
               muted: set[int], instruments: dict[int, tuple[int, int]],
               volumes: dict[int, int]) -> None:
    """Grava uma cópia do ritmo com a mixagem da prévia.

    Só bytes de mixer e andamento mudam, em todas as seções. Notas, tamanho e
    o arquivo de origem ficam iguais. Parte desligada vai com volume zero.
    """
    target = Path(target)
    try:
        data = bytearray(rhythm.path.read_bytes())
    except OSError as exc:
        raise AC7Error(f"Não foi possível ler o ritmo: {exc}") from exc
    if len(data) != rhythm.file_size:
        raise AC7Error("O arquivo mudou no disco. Recarregue antes de exportar.")

    if rhythm.tempo_offset >= 0:
        data[rhythm.tempo_offset] = max(20, min(255, int(tempo)))
    for part, offset in rhythm.mixer_refs:
        if part in instruments:
            data[offset], data[offset + 1] = instruments[part]
        if part in muted:
            data[offset + 2] = 0
        elif part in volumes:
            data[offset + 2] = max(0, min(127, int(volumes[part])))

    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(data)
        load_ac7(temporary)
        os.replace(temporary, target)
    except OSError as exc:
        raise AC7Error(f"Não foi possível salvar o ritmo: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
