import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class Cli(unittest.TestCase):
    def test_cli_existing_file_prints_the_line_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path: Path = Path(tmp) / "sample.txt"
            path.write_text("a\n\nb\n", encoding="utf-8")
            run: subprocess.CompletedProcess[str] = subprocess.run(
                [sys.executable, "-m", "lab.cli", str(path)], capture_output=True, text=True, check=False
            )
        self.assertEqual(run.stdout.strip(), "2")

    def test_cli_missing_file_prints_an_error(self) -> None:
        run: subprocess.CompletedProcess[str] = subprocess.run(
            [sys.executable, "-m", "lab.cli", "missing.txt"], capture_output=True, text=True, check=False
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("file not found: missing.txt", run.stderr)
