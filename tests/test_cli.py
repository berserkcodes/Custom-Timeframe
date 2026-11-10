"""Offline checks for the public entry point and its setup documentation."""

from pathlib import Path
import importlib
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_documents_setup_and_execution(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "python -m venv .venv",
            "Activate.ps1",
            "python -m pip install -e .",
            "requirements-build.txt",
            "requirements.lock.txt",
            "--no-deps --no-build-isolation",
            "python main.py --help",
            "Scripts\\python.exe",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, result.stdout)

    def test_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ChronoChart 0.1.0")

    def test_no_arguments_shows_help(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_unknown_argument_is_rejected(self) -> None:
        result = self.run_cli("--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_processing_modules_are_importable(self) -> None:
        for name in ("providers", "normalization", "aggregation", "timeframes", "cli"):
            with self.subTest(module=name):
                module = importlib.import_module(f"chronochart.{name}")
                self.assertIsNotNone(module.__doc__)

    def test_installed_module_help_outside_repository(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "chronochart", "--help"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ChronoChart", result.stdout)

    def test_console_script_help_outside_repository(self) -> None:
        script = Path(sys.executable).parent / (
            "chronochart.exe" if sys.platform == "win32" else "chronochart"
        )
        result = subprocess.run(
            [str(script), "--help"], cwd=ROOT.parent,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ChronoChart", result.stdout)

    def test_fixture_aggregation_demo(self) -> None:
        result = self.run_cli("--demo-aggregation")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Offline fixture candles:", result.stdout)
        self.assertIn("600.0", result.stdout)
        self.assertIn("900.0", result.stdout)
        self.assertIn("is_complete", result.stdout)

    def test_fixture_aggregation_demo_drop(self) -> None:
        result = self.run_cli("--demo-aggregation", "--incomplete", "drop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("600.0", result.stdout)
        self.assertNotIn("900.0", result.stdout)
        self.assertIn("False", result.stdout)


if __name__ == "__main__":
    unittest.main()
