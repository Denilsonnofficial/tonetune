from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ac7 import AC7Error, export_ac7, load_ac7  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "examples" / "DEMO.AC7"


class DemoAc7Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rhythm = load_ac7(DEMO)

    def test_metadata(self) -> None:
        rhythm = self.rhythm
        self.assertEqual(rhythm.display_name, "TONETUN")
        self.assertEqual(rhythm.tempo, 100)
        self.assertEqual(rhythm.signature, "4/4")
        self.assertEqual(len(rhythm.sections), 12)
        self.assertGreater(rhythm.file_size, 1000)

    def test_sections(self) -> None:
        var1 = self.rhythm.section(2)
        self.assertIsNotNone(var1)
        self.assertEqual(var1.name, "Variação 1")
        self.assertEqual(var1.measures, 4)
        self.assertEqual(var1.total_ticks, 4 * 4 * 96)
        self.assertGreater(var1.note_count, 50)
        self.assertEqual(var1.active_parts(), [2, 3, 4, 5])

    def test_empty_sections(self) -> None:
        for index in (7, 8, 9, 10, 11, 12):
            section = self.rhythm.section(index)
            self.assertIsNotNone(section)
            self.assertTrue(section.is_empty)

    def test_drum_notes_are_short(self) -> None:
        section = self.rhythm.section(2)
        lengths = [note.length for note in section.notes_for_part(2)]
        self.assertTrue(lengths)
        self.assertLessEqual(max(lengths), 48)

    def test_mixer(self) -> None:
        section = self.rhythm.section(2)
        strings = section.mixer_for_part(5)
        self.assertEqual(strings.patch, 49)
        self.assertEqual(strings.bank, 1)
        self.assertGreater(strings.volume, 0)

    def test_invalid_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ruim.AC7"
            path.write_bytes(b"AC07" + b"\x00" * 40)
            with self.assertRaises(AC7Error):
                load_ac7(path)


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.rhythm = load_ac7(DEMO)
        self.target = Path(self.folder.name) / "SAIDA.AC7"

    def export(self, tempo=None, muted=(), instruments=None, volumes=None):
        export_ac7(self.rhythm, self.target, tempo or self.rhythm.tempo,
                   set(muted), instruments or {}, volumes or {})
        return load_ac7(self.target)

    def test_without_changes_is_identical(self) -> None:
        self.export()
        self.assertEqual(self.target.read_bytes(), DEMO.read_bytes())

    def test_changes_reach_every_section(self) -> None:
        result = self.export(tempo=132, muted={7},
                             instruments={5: (48, 0), 2: (3, 120)},
                             volumes={3: 64})
        self.assertEqual(result.tempo, 132)
        for section in result.sections:
            for part, mixer in section.mixers.items():
                before = self.rhythm.section(section.index).mixers[part]
                if part == 5:
                    self.assertEqual((mixer.patch, mixer.bank), (48, 0))
                if part == 2:
                    self.assertEqual((mixer.patch, mixer.bank), (3, 120))
                if part == 3:
                    self.assertEqual(mixer.volume, 64)
                if part == 7:
                    self.assertEqual(mixer.volume, 0)
                if part in (1, 4, 6, 8):
                    self.assertEqual(mixer, before)

    def test_notes_and_size_do_not_change(self) -> None:
        result = self.export(instruments={3: (34, 0)}, volumes={2: 90})
        self.assertEqual(result.file_size, self.rhythm.file_size)
        for section in result.sections:
            original = self.rhythm.section(section.index)
            self.assertEqual([t.notes for t in section.tracks],
                             [t.notes for t in original.tracks])

    def test_source_is_untouched(self) -> None:
        before = DEMO.read_bytes()
        self.export(volumes={2: 1})
        self.assertEqual(DEMO.read_bytes(), before)

    def test_tempo_fits_one_byte(self) -> None:
        self.assertEqual(self.export(tempo=300).tempo, 255)


if __name__ == "__main__":
    unittest.main()
