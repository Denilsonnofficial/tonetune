"""Renderização e reprodução das seções de um ritmo AC7."""
from __future__ import annotations

import array
import hashlib
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import gi
import mido
from mido import Message, MetaMessage, MidiFile, bpm2tempo

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from ac7 import AC7_PPQ, Rhythm, Section  # noqa: E402

PPQ = 480
TICK_FACTOR = PPQ // AC7_PPQ  # 5
CHANNELS = {3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7}

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


def _active(part: int, muted: set[int], solo: set[int]) -> bool:
    if part in muted:
        return False
    return not solo or part in solo


def _append_controls(track: mido.MidiTrack, channel: int, mixer,
                     time: int = 0) -> None:
    for control, value in (
        (7, mixer.volume),
        (10, mixer.pan),
        (91, mixer.reverb),
        (93, mixer.chorus),
    ):
        track.append(Message(
            "control_change", channel=channel, control=control,
            value=max(0, min(127, int(value))), time=time))


def _append_notes(track: mido.MidiTrack, notes, channel: int,
                  total_ticks: int) -> None:
    events: list[tuple[int, int, Message]] = []
    for note in notes:
        start = max(0, int(note.tick) * TICK_FACTOR)
        end = min((int(note.tick) + max(1, int(note.length))) * TICK_FACTOR,
                  total_ticks)
        if start >= total_ticks or end <= start:
            continue
        events.append((start, 1, Message(
            "note_on", channel=channel,
            note=max(0, min(127, int(note.pitch))),
            velocity=max(1, min(127, int(note.velocity))))))
        events.append((end, 0, Message(
            "note_off", channel=channel,
            note=max(0, min(127, int(note.pitch))), velocity=0)))
    events.sort(key=lambda item: (item[0], item[1]))
    last = 0
    for tick, _priority, message in events:
        track.append(message.copy(time=max(0, tick - last)))
        last = tick
    track.append(MetaMessage("end_of_track", time=max(0, total_ticks - last)))


def build_midi(rhythm: Rhythm, section: Section, tempo: int,
               muted: set[int] | None = None,
               solo: set[int] | None = None) -> MidiFile:
    """Converte a seção do AC7 em um MIDI de prévia (aproximação GM)."""
    muted = muted or set()
    solo = solo or set()
    total_ticks = section.total_ticks * TICK_FACTOR
    mid = MidiFile(ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(MetaMessage(
        "time_signature", numerator=section.beats,
        denominator=section.denominator, time=0))
    conductor.append(MetaMessage(
        "set_tempo", tempo=bpm2tempo(max(20, min(300, int(tempo)))), time=0))
    conductor.append(MetaMessage("marker", text="end", time=total_ticks))

    note_total = 0

    # As duas partes de bateria compartilham o canal 10 do GM. Na prévia,
    # usamos o kit da parte 2 quando ela está ativa (como no editor).
    drum_parts = [part for part in (1, 2)
                  if _active(part, muted, solo)
                  and section.note_count_for_part(part)]
    if drum_parts:
        kit_part = 2 if 2 in drum_parts else drum_parts[0]
        mixer = section.mixer_for_part(kit_part)
        notes = []
        for part in drum_parts:
            notes.extend(section.notes_for_part(part))
        if notes:
            track = mido.MidiTrack()
            mid.tracks.append(track)
            channel = 9
            track.append(Message(
                "program_change", channel=channel,
                program=max(0, min(127, mixer.patch)), time=0))
            _append_controls(track, channel, mixer)
            _append_notes(track, notes, channel, total_ticks)
            note_total += len(notes)

    for part in range(3, 9):
        if not _active(part, muted, solo):
            continue
        notes = section.notes_for_part(part)
        if not notes:
            continue
        mixer = section.mixer_for_part(part)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        channel = CHANNELS[part]
        track.append(Message(
            "program_change", channel=channel,
            program=max(0, min(127, mixer.patch)), time=0))
        _append_controls(track, channel, mixer)
        _append_notes(track, notes, channel, total_ticks)
        note_total += len(notes)

    if note_total == 0:
        raise AudioError(
            "Nenhuma parte ativa tem notas nesta seção. "
            "Desmute uma parte ou escolha outra seção."
        )
    return mid


def _trim_wav(src: Path, dst: Path, duration: float) -> None:
    """Corta o rabo do reverb para o loop fechar no tempo certo."""
    try:
        with wave.open(str(src), "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"Áudio gerado inválido: {exc}") from exc

    channels = params.nchannels
    width = params.sampwidth
    rate = params.framerate
    target = max(1, int(round(duration * rate)))
    frame_size = channels * width
    available = len(frames) // max(1, frame_size)
    if available > target:
        frames = frames[:target * frame_size]
        available = target

    if width == 2 and available > 4:
        samples = array.array("h")
        samples.frombytes(frames)
        fade = min(max(1, int(0.015 * rate)), available)
        for index in range(fade):
            gain = 1.0 - (index / fade)
            frame = available - fade + index
            for channel in range(channels):
                position = frame * channels + channel
                if 0 <= position < len(samples):
                    samples[position] = int(samples[position] * gain)
        frames = samples.tobytes()

    with wave.open(str(dst), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(frames)


class Renderer:
    """Gera WAVs em cache para cada seção, com as partes mutadas/solo."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (
            Path(GLib.get_user_cache_dir()) / "tonetune")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, rhythm: Rhythm, section: Section, tempo: int,
                  muted: set[int], solo: set[int]) -> str:
        try:
            content = rhythm.path.read_bytes()
        except OSError:
            content = str(rhythm.path).encode("utf-8")
        digest = hashlib.sha256()
        digest.update(content)
        digest.update(f"{section.index}|{tempo}|".encode("ascii"))
        digest.update(",".join(map(str, sorted(muted))).encode("ascii"))
        digest.update(b"|")
        digest.update(",".join(map(str, sorted(solo))).encode("ascii"))
        return digest.hexdigest()[:20]

    def cache_path(self, rhythm: Rhythm, section: Section, tempo: int,
                   muted: set[int], solo: set[int]) -> Path:
        key = self.cache_key(rhythm, section, tempo, muted, solo)
        return self.cache_dir / f"section_{key}.wav"

    def render(self, rhythm: Rhythm, section: Section, tempo: int,
               muted: set[int] | None = None,
               solo: set[int] | None = None) -> Path:
        muted = muted or set()
        solo = solo or set()
        final = self.cache_path(rhythm, section, tempo, muted, solo)
        if final.is_file() and final.stat().st_size > 1024:
            return final

        mid_fd, mid_name = tempfile.mkstemp(
            prefix="tonetune_", suffix=".mid", dir=self.cache_dir)
        wav_fd, wav_name = tempfile.mkstemp(
            prefix="tonetune_", suffix=".raw.wav", dir=self.cache_dir)
        os.close(mid_fd)
        os.close(wav_fd)
        mid_path = Path(mid_name)
        raw_wav = Path(wav_name)
        final_tmp = final.with_suffix(".tmp.wav")
        try:
            midi = build_midi(rhythm, section, tempo, muted, solo)
            midi.save(mid_path)
            command = [
                "fluidsynth", "-ni", "-q", "-g", "0.75", "-r", "44100",
                "-T", "wav", "-F", str(raw_wav), str(_soundfont()),
                str(mid_path),
            ]
            try:
                proc = subprocess.run(command, capture_output=True, text=True,
                                      timeout=120)
            except FileNotFoundError as exc:
                raise AudioError(
                    "FluidSynth não encontrado. Instale o pacote fluidsynth."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise AudioError("A preparação do áudio demorou demais.") from exc
            if proc.returncode != 0:
                lines = (proc.stderr or proc.stdout or "").strip().splitlines()
                detail = lines[-1] if lines else "erro desconhecido"
                raise AudioError(f"FluidSynth falhou: {detail}")

            _trim_wav(raw_wav, final_tmp,
                      rhythm.duration(section, tempo))
            os.replace(final_tmp, final)
        finally:
            for path in (mid_path, raw_wav, final_tmp):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._cleanup()
        return final

    def _cleanup(self) -> None:
        try:
            files = sorted(self.cache_dir.glob("section_*.wav"),
                           key=lambda path: path.stat().st_mtime,
                           reverse=True)
            for old in files[40:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass


class PlaybackEngine:
    """Toca um WAV em loop com GStreamer."""

    def __init__(self, volume: float = 0.85) -> None:
        self._state = "stopped"
        self._loop = True
        self._error: str | None = None
        self.on_finished = None
        self.on_error = None
        self._duration = 0.0

        Gst.init(None)
        self._player = Gst.ElementFactory.make("playbin", "tonetune-playback")
        if self._player is None:
            raise AudioError("GStreamer playbin não está disponível.")
        self._player.set_property("volume", max(0.0, min(1.0, volume)))
        self._bus = self._player.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        self._player.connect("about-to-finish", self._on_about_to_finish)

    @property
    def state(self) -> str:
        return self._state

    @property
    def playing(self) -> bool:
        return self._state == "playing"

    @property
    def paused(self) -> bool:
        return self._state == "paused"

    @property
    def active(self) -> bool:
        return self._state != "stopped"

    @property
    def loop(self) -> bool:
        return self._loop

    def set_loop(self, enabled: bool) -> None:
        self._loop = bool(enabled)

    def set_volume(self, volume: float) -> None:
        try:
            self._player.set_property(
                "volume", max(0.0, min(1.0, float(volume))))
        except Exception:
            pass

    def play(self, wav_path: Path, loop: bool = True) -> None:
        self.stop()
        self._loop = bool(loop)
        self._error = None
        self._player.set_property("uri", Path(wav_path).resolve().as_uri())
        result = self._player.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._player.set_state(Gst.State.NULL)
            raise AudioError("Não foi possível iniciar a reprodução.")
        self._state = "playing"

    def pause(self) -> None:
        if self._state == "playing":
            self._player.set_state(Gst.State.PAUSED)
            self._state = "paused"

    def resume(self) -> None:
        if self._state == "paused":
            self._player.set_state(Gst.State.PLAYING)
            self._state = "playing"

    def stop(self) -> None:
        self._state = "stopped"
        try:
            self._player.set_state(Gst.State.NULL)
        except Exception:
            pass

    def position(self) -> float:
        if self._state == "stopped":
            return 0.0
        try:
            ok, position = self._player.query_position(Gst.Format.TIME)
            if ok and position > 0:
                return position / Gst.SECOND
        except Exception:
            pass
        return 0.0

    def close(self) -> None:
        self.stop()
        try:
            self._bus.remove_signal_watch()
        except Exception:
            pass
        self._player.set_state(Gst.State.NULL)

    def _on_about_to_finish(self, _player) -> None:
        if self._state == "playing" and self._loop:
            try:
                self._player.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, 0)
            except Exception:
                pass

    def _on_bus_message(self, _bus, message) -> None:
        if message.type == Gst.MessageType.EOS:
            if self._state == "playing" and self._loop:
                try:
                    self._player.seek_simple(
                        Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, 0)
                    self._player.set_state(Gst.State.PLAYING)
                except Exception:
                    self._finish()
            else:
                self._finish()
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._error = str(error)
            self._state = "stopped"
            try:
                self._player.set_state(Gst.State.NULL)
            except Exception:
                pass
            if self.on_error:
                self.on_error(self._error)

    def _finish(self) -> None:
        if self._state == "stopped":
            return
        self._state = "stopped"
        try:
            self._player.set_state(Gst.State.NULL)
        except Exception:
            pass
        if self.on_finished:
            self.on_finished()
