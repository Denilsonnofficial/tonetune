from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ac7 import load_ac7  # noqa: E402
from audio import AudioError, Renderer, SOUNDFONTS, build_midi  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "examples" / "DEMO.AC7"


class MidiBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rhythm = load_ac7(DEMO)
        cls.section = cls.rhythm.section(2)

    def test_build_midi(self) -> None:
        midi = build_midi(self.rhythm, self.section, self.rhythm.tempo)
        self.assertGreaterEqual(len(midi.tracks), 2)
        note_ons = sum(
            1 for track in midi.tracks for message in track
            if message.type == "note_on" and message.velocity > 0)
        self.assertGreater(note_ons, 50)

    def test_all_muted_raises(self) -> None:
        with self.assertRaises(AudioError):
            build_midi(self.rhythm, self.section, self.rhythm.tempo,
                       muted=set(range(1, 9)))


@unittest.skipUnless(shutil.which("fluidsynth")
                     and any(path.is_file() for path in SOUNDFONTS),
                     "FluidSynth/SoundFont não disponível")
class RenderTest(unittest.TestCase):
    def test_render_section(self) -> None:
        rhythm = load_ac7(DEMO)
        section = rhythm.section(2)
        with tempfile.TemporaryDirectory() as folder:
            renderer = Renderer(cache_dir=Path(folder))
            wav = renderer.render(rhythm, section, rhythm.tempo)
            self.assertTrue(wav.is_file())
            self.assertGreater(wav.stat().st_size, 10000)


if __name__ == "__main__":
    unittest.main()
