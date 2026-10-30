"""Motor de reprodução ao vivo dos ritmos AC7.

O ritmo é sequenciado em blocos curtos direto no FluidSynth, como o teclado
faz: as transições não têm emenda e andamento, acorde, mixagem e timbres mudam
na hora, sem preparar áudio antes.
"""
from __future__ import annotations

import bisect
import ctypes
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from ac7 import AC7_PPQ, Rhythm, Section  # noqa: E402
from arranger import Arranger, Chord  # noqa: E402

RATE = 44100
BLOCK = 128  # quadros por bloco; define a resolução do tempo (2,9 ms)
PUSH_BLOCKS = 4
TAIL_SECONDS = 3.0
DRUM_HOLD = AC7_PPQ * 4  # bateria não abafa no note-off, deixa o prato soar
CHANNELS = {1: 9, 2: 10, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7}
DRUM_PARTS = (1, 2)
DRUM_BANK = 128
CHANNEL_TYPE_DRUM = 1

# Kits do GM mais parecidos com cada set do CT-X800 (pelo número do programa).
KIT_MAP = {
    8: 8, 9: 24, 16: 16, 17: 16, 24: 24, 25: 25, 26: 24, 27: 24, 28: 24,
    29: 24, 30: 25, 31: 24, 32: 32, 33: 25, 34: 24, 35: 24, 40: 40, 48: 48,
    60: 56, 61: 56,
}

SOUNDFONTS = (
    Path("/usr/share/sounds/sf2/default-GM.sf2"),
    Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
)


class AudioError(Exception):
    """Falha ao preparar ou tocar o áudio."""


def _soundfont() -> Path:
    for path in SOUNDFONTS:
        if path.is_file():
            return path
    raise AudioError(
        "SoundFont GM não encontrado. Instale o pacote fluid-soundfont-gm."
    )


def _load_library() -> ctypes.CDLL:
    try:
        lib = ctypes.CDLL("libfluidsynth.so.3")
    except OSError as exc:
        raise AudioError(
            "FluidSynth não encontrado. Instale o pacote fluidsynth.") from exc
    ptr, num, text = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p
    for name, restype, argtypes in (
        ("new_fluid_settings", ptr, ()),
        ("delete_fluid_settings", None, (ptr,)),
        ("fluid_settings_setnum", num, (ptr, text, ctypes.c_double)),
        ("fluid_settings_setint", num, (ptr, text, num)),
        ("new_fluid_synth", ptr, (ptr,)),
        ("delete_fluid_synth", None, (ptr,)),
        ("fluid_synth_sfload", num, (ptr, text, num)),
        ("fluid_synth_set_channel_type", num, (ptr, num, num)),
        ("fluid_synth_program_select", num, (ptr, num, num, num, num)),
        ("fluid_synth_get_program", num,
         (ptr, num, ctypes.POINTER(num), ctypes.POINTER(num),
          ctypes.POINTER(num))),
        ("fluid_synth_cc", num, (ptr, num, num, num)),
        ("fluid_synth_noteon", num, (ptr, num, num, num)),
        ("fluid_synth_noteoff", num, (ptr, num, num)),
        ("fluid_synth_write_s16", num,
         (ptr, num, ptr, num, num, ptr, num, num)),
    ):
        function = getattr(lib, name)
        function.restype = restype
        function.argtypes = argtypes
    return lib


@dataclass(frozen=True)
class Position:
    """Onde o ritmo está para quem ouve."""
    section: int
    tick: float
    next_section: int | None


class LiveSynth:
    """Sequenciador e sintetizador; gera o áudio bloco a bloco."""

    def __init__(self) -> None:
        self._lib = _load_library()
        self._settings = self._lib.new_fluid_settings()
        self._lib.fluid_settings_setnum(
            self._settings, b"synth.sample-rate", float(RATE))
        self._lib.fluid_settings_setnum(self._settings, b"synth.gain", 0.65)
        self._lib.fluid_settings_setint(
            self._settings, b"synth.polyphony", 512)
        self._synth = self._lib.new_fluid_synth(self._settings)
        if not self._synth:
            raise AudioError("Não foi possível iniciar o FluidSynth.")
        for part in DRUM_PARTS:
            self._lib.fluid_synth_set_channel_type(
                self._synth, CHANNELS[part], CHANNEL_TYPE_DRUM)

        self.lock = threading.RLock()
        self.arranger: Arranger | None = None
        self.tempo = 120
        self.chord = Chord()
        self.muted: set[int] = set()
        self.solo: set[int] = set()
        self.instruments: dict[int, tuple[int, int]] = {}
        self.volumes: dict[int, int] = {}
        self.frames = 0
        self.timeline: deque[tuple[int, Position]] = deque(maxlen=512)
        self._sfont = -1
        self._section: Section | None = None
        self._clock = 0.0
        self._sounding: list[list] = []
        self._events: dict[int, tuple[list[int], list]] = {}
        self._buffer = (ctypes.c_int16 * (BLOCK * 2))()

    @property
    def playing(self) -> bool:
        return self.arranger is not None and self.arranger.current is not None

    def load_soundfont(self) -> None:
        sfont = self._lib.fluid_synth_sfload(
            self._synth, str(_soundfont()).encode(), 1)
        if sfont < 0:
            raise AudioError("Não foi possível abrir o SoundFont.")
        with self.lock:
            self._sfont = sfont
            if self.arranger is not None and self.arranger.current is not None:
                self._apply_section(self.arranger.current)

    def set_rhythm(self, rhythm: Rhythm) -> None:
        with self.lock:
            repeat = self.arranger.repeat if self.arranger else False
            self.stop()
            self.arranger = Arranger(rhythm)
            self.arranger.repeat = repeat
            self._events.clear()

    def start(self, index: int) -> bool:
        with self.lock:
            if self.arranger is None or not self.arranger.start(index):
                return False
            self._section = None
            return True

    def press(self, index: int) -> bool:
        with self.lock:
            return self.arranger is not None and self.arranger.press(index)

    def stop(self) -> None:
        with self.lock:
            if self.arranger is not None:
                self.arranger.stop()
            self._release(lambda _item: True)
            self.timeline.clear()

    def set_repeat(self, enabled: bool) -> None:
        with self.lock:
            if self.arranger is not None:
                self.arranger.repeat = enabled

    def set_chord(self, chord: Chord) -> None:
        """Troca o acorde já nas notas que estão soando."""
        with self.lock:
            self.chord = chord
            for item in self._sounding:
                if not item[5]:
                    continue
                pitch = chord.apply(item[2])
                if pitch != item[1]:
                    self._lib.fluid_synth_noteoff(self._synth, item[0], item[1])
                    self._lib.fluid_synth_noteon(
                        self._synth, item[0], pitch, item[3])
                    item[1] = pitch

    def set_mix(self, muted: set[int], solo: set[int],
                instruments: dict[int, tuple[int, int]],
                volumes: dict[int, int]) -> None:
        with self.lock:
            self.muted = set(muted)
            self.solo = set(solo)
            self.instruments = dict(instruments)
            self.volumes = dict(volumes)
            if self._section is not None:
                self._apply_section(self._section)

    def _audible(self, part: int) -> bool:
        if part in self.muted:
            return False
        return not self.solo or part in self.solo

    def _select_program(self, part: int, program: int) -> None:
        if self._sfont < 0:
            return
        if part in DRUM_PARTS:
            bank, preset = DRUM_BANK, KIT_MAP.get(program, 0)
        else:
            bank, preset = 0, max(0, min(127, program))
        self._lib.fluid_synth_program_select(
            self._synth, CHANNELS[part], self._sfont, bank, preset)

    def _apply_section(self, section: Section) -> None:
        for part, channel in CHANNELS.items():
            mixer = section.mixer_for_part(part)
            override = self.instruments.get(part)
            self._select_program(part, override[0] if override else mixer.patch)
            volume = self.volumes.get(part, mixer.volume)
            if not self._audible(part):
                volume = 0
            for control, value in ((7, volume), (10, mixer.pan),
                                   (91, mixer.reverb), (93, mixer.chorus)):
                self._lib.fluid_synth_cc(self._synth, channel, control,
                                         max(0, min(127, int(value))))

    def _section_events(self, section: Section) -> tuple[list[int], list]:
        cached = self._events.get(section.index)
        if cached is None:
            flat = sorted(
                ((note.tick, track.part, id(note), note, track)
                 for track in section.tracks for note in track.notes
                 if note.tick < section.total_ticks),
                key=lambda item: item[:3])
            cached = ([item[0] for item in flat], flat)
            self._events[section.index] = cached
        return cached

    def _release(self, match) -> None:
        keep = []
        for item in self._sounding:
            if match(item):
                self._lib.fluid_synth_noteoff(self._synth, item[0], item[1])
            else:
                keep.append(item)
        self._sounding = keep

    def _note_on(self, section: Section, tick: float, part: int, note, track,
                 clock: float) -> None:
        drum = part in DRUM_PARTS
        if not drum and ((track.major_only and self.chord.minor)
                         or (track.minor_only and not self.chord.minor)):
            return
        channel = CHANNELS[part]
        sync = not drum and track.chord_sync
        pitch = self.chord.apply(note.pitch) if sync else note.pitch
        if drum:
            end = clock + DRUM_HOLD
        else:
            end = clock + min(max(1, note.length), section.total_ticks - tick)
        self._sounding = [item for item in self._sounding
                          if item[0] != channel or item[1] != pitch]
        self._lib.fluid_synth_noteon(self._synth, channel, pitch,
                                     max(1, min(127, note.velocity)))
        self._sounding.append([channel, pitch, note.pitch, note.velocity,
                               end, sync])

    def _play(self, ticks: float) -> None:
        clock = self._clock
        for segment in self.arranger.advance(ticks):
            section = segment.section
            if section is not self._section:
                drums = tuple(CHANNELS[part] for part in DRUM_PARTS)
                self._release(lambda item: item[0] not in drums)
                self._section = section
                self._apply_section(section)
            ticks_list, flat = self._section_events(section)
            first = bisect.bisect_left(ticks_list, segment.start)
            last = bisect.bisect_left(ticks_list, segment.end)
            for tick, part, _order, note, track in flat[first:last]:
                self._note_on(section, tick, part, note, track,
                              clock + tick - segment.start)
            clock += segment.end - segment.start
        self._clock += ticks
        now = self._clock
        self._release(lambda item: item[4] <= now)
        if not self.playing:
            self._section = None
            self._release(lambda _item: True)

    def render(self) -> bytes:
        """Avança um bloco e devolve o áudio dele (S16LE estéreo)."""
        with self.lock:
            if self.playing:
                arranger = self.arranger
                self.timeline.append((self.frames, Position(
                    arranger.current.index, arranger.tick,
                    arranger.next_index)))
                self._play(BLOCK * self.tempo * AC7_PPQ / (60.0 * RATE))
            self._lib.fluid_synth_write_s16(
                self._synth, BLOCK, self._buffer, 0, 2, self._buffer, 1, 2)
            self.frames += BLOCK
        return bytes(self._buffer)

    def channel_bank(self, part: int) -> int:
        sfont, bank, preset = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int())
        self._lib.fluid_synth_get_program(
            self._synth, CHANNELS[part], ctypes.byref(sfont),
            ctypes.byref(bank), ctypes.byref(preset))
        return bank.value

    def close(self) -> None:
        self._lib.delete_fluid_synth(self._synth)
        self._lib.delete_fluid_settings(self._settings)


class PlaybackEngine:
    """Leva o áudio do LiveSynth para a saída de som com GStreamer."""

    def __init__(self, volume: float = 0.85) -> None:
        self.on_finished = None
        self.on_error = None
        self._paused = False
        self._was_playing = False
        self._closing = False
        self._worker: threading.Thread | None = None
        self._ready = threading.Event()
        self._frames_base = 0
        self._bytes_limit = BLOCK * PUSH_BLOCKS * 4 * 2

        Gst.init(None)
        self.live = LiveSynth()
        self._pipeline = Gst.Pipeline.new("tonetune-playback")
        self._source = Gst.ElementFactory.make("appsrc", "source")
        convert = Gst.ElementFactory.make("audioconvert", "convert")
        resample = Gst.ElementFactory.make("audioresample", "resample")
        self._volume = Gst.ElementFactory.make("volume", "volume")
        sink = Gst.ElementFactory.make("pulsesink", "sink")
        if sink is not None:
            sink.set_property("buffer-time", 80000)
            sink.set_property("latency-time", 10000)
        else:
            sink = Gst.ElementFactory.make("autoaudiosink", "sink")
        elements = (self._source, convert, resample, self._volume, sink)
        if any(element is None for element in elements):
            raise AudioError("Elementos do GStreamer não estão disponíveis.")
        self._source.set_property("format", Gst.Format.TIME)
        self._source.set_property("caps", Gst.Caps.from_string(
            f"audio/x-raw,format=S16LE,rate={RATE},channels=2,"
            "layout=interleaved"))
        self._volume.set_property("volume", max(0.0, min(1.0, volume)))
        for element in elements:
            self._pipeline.add(element)
        for source, target in zip(elements, elements[1:]):
            source.link(target)
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        threading.Thread(target=self._prepare, daemon=True).start()

    def _prepare(self) -> None:
        try:
            self.live.load_soundfont()
        except AudioError as exc:
            GLib.idle_add(self._report_error, str(exc))
        else:
            self._ready.set()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def state(self) -> str:
        if not self.live.playing:
            return "stopped"
        return "paused" if self._paused else "playing"

    @property
    def playing(self) -> bool:
        return self.state == "playing"

    @property
    def paused(self) -> bool:
        return self.state == "paused"

    @property
    def active(self) -> bool:
        return self.live.playing

    def set_rhythm(self, rhythm: Rhythm) -> None:
        self.stop()
        self.live.set_rhythm(rhythm)

    def set_volume(self, volume: float) -> None:
        self._volume.set_property(
            "volume", max(0.0, min(1.0, float(volume))))

    def start(self, index: int) -> None:
        if self._paused:
            self.resume()
        if not self.live.start(index):
            raise AudioError("Esta seção não tem notas.")
        self._was_playing = True
        self._ensure_worker()

    def press(self, index: int) -> bool:
        return self.live.press(index)

    def pause(self) -> None:
        if self.playing:
            self._paused = True
            self._pipeline.set_state(Gst.State.PAUSED)

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        """Para o ritmo e deixa a cauda das notas e do reverb terminar."""
        self._was_playing = False
        self.resume()
        self.live.stop()

    def position(self) -> Position | None:
        """Posição do que está saindo na caixa agora, não do que foi gerado."""
        ok, nanoseconds = self._pipeline.query_position(Gst.Format.TIME)
        with self.live.lock:
            timeline = self.live.timeline
            if not timeline:
                return None
            heard = None
            if ok and nanoseconds > 0:
                frame = self._frames_base + nanoseconds * RATE // Gst.SECOND
                for frames, position in reversed(timeline):
                    if frames <= frame:
                        heard = position
                        break
            return heard or timeline[0][1]

    def close(self) -> None:
        self._closing = True
        self.live.stop()
        worker = self._worker
        if worker is not None:
            self._paused = False
            worker.join(timeout=1)
        self._bus.remove_signal_watch()
        self._pipeline.set_state(Gst.State.NULL)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _run(self) -> None:
        self._ready.wait()
        self._frames_base = self.live.frames
        self._pipeline.set_state(Gst.State.PLAYING)
        pushed = 0
        silence = 0.0
        pending = bytearray()
        while not self._closing and (self.live.playing
                                     or silence < TAIL_SECONDS):
            if (self._paused or self._source.get_property(
                    "current-level-bytes") > self._bytes_limit):
                time.sleep(0.002)
                continue
            for _block in range(PUSH_BLOCKS):
                pending += self.live.render()
            buffer = Gst.Buffer.new_wrapped(bytes(pending))
            frames = len(pending) // 4
            buffer.pts = pushed * Gst.SECOND // RATE
            buffer.duration = frames * Gst.SECOND // RATE
            pending.clear()
            pushed += frames
            self._source.emit("push-buffer", buffer)
            if self.live.playing:
                silence = 0.0
            else:
                silence += frames / RATE
                if self._was_playing:
                    self._was_playing = False
                    GLib.idle_add(self._report_finished)
        if not self._closing:
            self._pipeline.set_state(Gst.State.NULL)
            if self.live.playing:  # começou enquanto a cauda terminava
                self._run()

    def _report_finished(self) -> bool:
        if self.on_finished:
            self.on_finished()
        return False

    def _report_error(self, message: str) -> bool:
        if self.on_error:
            self.on_error(message)
        return False

    def _on_bus_message(self, _bus, message) -> None:
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.live.stop()
            self._pipeline.set_state(Gst.State.NULL)
            self._report_error(str(error))
