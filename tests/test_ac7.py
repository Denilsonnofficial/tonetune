from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ac7 import AC7Error, load_ac7  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
