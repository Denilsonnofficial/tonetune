from __future__ import annotations

import array
import copy
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ac7 import Note, Track, load_ac7  # noqa: E402
from arranger import Arranger, Chord  # noqa: E402
from audio import BLOCK, RATE, SOUNDFONTS, LiveSynth  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "examples" / "DEMO.AC7"
BAR = 384


def run(arranger: Arranger, ticks: float, step: float = 7.0) -> list[int]:
    """Avança e devolve as seções visitadas, em ordem."""
    visited = []
    for _ in range(int(ticks / step)):
        for segment in arranger.advance(step):
            if not visited or visited[-1] != segment.section.index:
                visited.append(segment.section.index)
    return visited


class ArrangerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.arranger = Arranger(load_ac7(DEMO))

    def test_intro_goes_to_variation(self) -> None:
        self.arranger.start(1)
        self.assertEqual(self.arranger.next_index, 2)
        self.assertEqual(run(self.arranger, BAR * 3), [1, 2])

    def test_variation_pressed_during_intro_is_queued(self) -> None:
        self.arranger.start(1)
        self.arranger.press(3)
        self.assertEqual(self.arranger.next_index, 3)
        self.assertEqual(run(self.arranger, BAR * 3), [1, 3])

    def test_variation_loops(self) -> None:
        self.arranger.start(2)
        self.assertEqual(run(self.arranger, BAR * 9), [2])

    def test_variation_changes_on_next_bar(self) -> None:
        self.arranger.start(2)
        self.arranger.advance(100)
        self.arranger.press(3)
        self.arranger.advance(BAR - 101)
        self.assertEqual(self.arranger.current.index, 2)
        self.arranger.advance(2)
        self.assertEqual(self.arranger.current.index, 3)
        self.assertLess(self.arranger.tick, 2)

    def test_same_variation_plays_fill_and_returns(self) -> None:
        self.arranger.start(2)
        self.arranger.advance(10)
        self.arranger.press(2)
        self.arranger.advance(87)
        self.assertEqual(self.arranger.current.index, 4)
        self.assertAlmostEqual(self.arranger.tick, 97, delta=1)
        self.assertEqual(self.arranger.next_index, 2)
        self.assertEqual(run(self.arranger, BAR), [4, 2])

    def test_fill_on_last_beat_waits_for_next_bar(self) -> None:
        self.arranger.start(2)
        self.arranger.advance(BAR - 10)
        self.arranger.press(2)
        self.arranger.advance(11)
        self.assertEqual(self.arranger.current.index, 4)
        self.assertLess(self.arranger.tick, 2)

    def test_ending_stops(self) -> None:
        self.arranger.start(2)
        self.arranger.press(6)
        self.assertEqual(run(self.arranger, BAR * 4), [2, 6])
        self.assertIsNone(self.arranger.current)

    def test_empty_section_is_ignored(self) -> None:
        self.arranger.start(2)
        self.assertFalse(self.arranger.press(8))

    def test_repeat_keeps_section_and_switches_now(self) -> None:
        self.arranger.repeat = True
        self.arranger.start(4)
        self.assertEqual(run(self.arranger, BAR * 3), [4])
        self.arranger.press(6)
        self.arranger.advance(1)
        self.assertEqual(self.arranger.current.index, 6)


class ChordTest(unittest.TestCase):
    def test_c_major_keeps_notes(self) -> None:
        self.assertEqual([Chord().apply(n) for n in (48, 52, 55, 71)],
                         [48, 52, 55, 71])

    def test_minor_lowers_third(self) -> None:
        self.assertEqual(Chord(0, "m").apply(52), 51)

    def test_root_uses_nearest_register(self) -> None:
        self.assertEqual(Chord(2).apply(48), 50)
        self.assertEqual(Chord(7).apply(48), 43)

    def test_seventh(self) -> None:
        self.assertEqual(Chord(0, "7").apply(59), 58)


@unittest.skipUnless(shutil.which("fluidsynth")
                     and any(path.is_file() for path in SOUNDFONTS),
                     "FluidSynth/SoundFont não disponível")
class LiveSynthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live = LiveSynth()
        cls.live.load_soundfont()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.live.close()

    def setUp(self) -> None:
        self.rhythm = load_ac7(DEMO)
        self.live.set_rhythm(self.rhythm)
        self.live.tempo = self.rhythm.tempo
        self.live.set_mix(set(), set(), {}, {})
        self.live.set_chord(Chord())

    def peak(self, seconds: float) -> int:
        samples = array.array("h")
        for _ in range(int(seconds * RATE / BLOCK)):
            samples.frombytes(self.live.render())
        return max(abs(value) for value in samples)

    def test_both_drum_parts_use_the_drum_bank(self) -> None:
        self.live.start(2)
        self.live.render()
        self.assertEqual(self.live.channel_bank(1), 128)
        self.assertEqual(self.live.channel_bank(2), 128)
        self.live.stop()

    def test_produces_sound(self) -> None:
        self.live.start(2)
        self.assertGreater(self.peak(1.0), 1000)
        self.live.stop()

    def test_solo_silences_other_parts(self) -> None:
        self.live.stop()
        self.peak(5.0)
        self.live.start(2)
        self.live.set_mix(set(), {8}, {}, {})
        self.assertLessEqual(self.peak(0.5), 2)  # só o dither
        self.live.stop()

    def test_ending_finishes_alone(self) -> None:
        self.live.tempo = 300
        self.live.start(6)
        self.peak(3.5)
        self.assertFalse(self.live.playing)

    def test_chord_moves_sounding_notes(self) -> None:
        section = copy.deepcopy(self.rhythm.section(2))
        section.tracks = [Track(part=3, notes=[Note(0, 48, 100, 200)])]
        self.rhythm.sections[1] = section
        self.live.set_rhythm(self.rhythm)
        self.live.start(2)
        self.live.render()
        self.live.set_chord(Chord(2))
        self.assertEqual(self.live._sounding[0][1], 50)
        self.live.stop()


if __name__ == "__main__":
    unittest.main()
