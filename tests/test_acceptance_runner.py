"""Safety checks for the acceptance harness itself (never runs live/install IO)."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_backend_acceptance as runner


class AcceptanceRunnerTests(unittest.TestCase):
    def test_only_powershell_fences_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("```python\nnot_a_shell_command()\n```\nplain text\n```powershell\npython main.py --help\n```\nmore text", encoding="utf-8")
            self.assertEqual(list(runner.shell_lines(path)), ["python main.py --help"])

    def test_quote_preservation_and_live_skip_classification(self):
        root = Path.cwd()
        category, args = runner.command('.\\.venv\\Scripts\\python.exe main.py --window-mode "3 calendar weeks"', root)
        self.assertEqual(category, "offline")
        self.assertEqual(args[-1], "3 calendar weeks")
        for line in ("python main.py --live-binance-check", "python main.py run --provider yfinance", "python main.py run --provider binance"):
            self.assertEqual(runner.command(line, root)[0], "live-skipped")

    def test_unknown_commands_and_escaping_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for line in ("powershell Remove-Item anything", "python arbitrary.py", "python -m pip uninstall anything", "python main.py run --output ../escape.json", "python main.py run --cache-dir ../cache"):
                with self.subTest(line=line), self.assertRaises(ValueError):
                    runner.command(line, Path(directory))

    def test_prepare_copies_only_explicit_files_and_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("README.md", "main.py", "pyproject.toml", "requirements-build.txt", "requirements.lock.txt", "ChronoChart_Brief.md", "src/pkg/a.py", "tests/a.py", "docs/a.md", ".git/not-copied", "data/not-copied"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            destination = root / "data" / "acceptance"
            with patch.object(runner, "ROOT", root), patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="test-revision\n")), redirect_stdout(io.StringIO()):
                runner.prepare(destination)
                with self.assertRaises(FileExistsError):
                    runner.prepare(destination)
            for mode in ("editable", "wheel"):
                self.assertTrue((destination / mode / "src/pkg/a.py").exists())
                self.assertFalse((destination / mode / ".git").exists())
                self.assertFalse((destination / mode / "data").exists())


if __name__ == "__main__":
    unittest.main()
