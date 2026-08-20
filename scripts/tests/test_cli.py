import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import unittest
from pathlib import Path
from overlord_py.cli import parse_cli

class CliTests(unittest.TestCase):
    def test_default_is_shell(self):
        result = parse_cli([], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 0)
        self.assertIsNotNone(result.options)
        self.assertEqual(result.options.command.value, "shell")

    def test_shell_explicit(self):
        result = parse_cli(["shell"], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 0)
        self.assertEqual(result.options.command.value, "shell")

    def test_zellij(self):
        result = parse_cli(["zellij"], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 0)
        self.assertEqual(result.options.command.value, "zellij")

    def test_help(self):
        result = parse_cli(["help"], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 0)
        self.assertIn("overlord", result.stdout.lower())

    def test_unknown_command(self):
        result = parse_cli(["web"], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 1)
        self.assertIn("unknown command", result.stderr)

    def test_unknown_option(self):
        result = parse_cli(["--config", "default"], env={}, repo_root=Path.cwd())
        self.assertEqual(result.status, 1)
        self.assertIn("unknown option", result.stderr)

if __name__ == "__main__":
    unittest.main()
