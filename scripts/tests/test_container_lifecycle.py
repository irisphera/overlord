import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from overlord_py.container_lifecycle import SETUP_OWNERSHIP_REPAIR_SCRIPT, ensure_running
from overlord_py.engine import CommandResult
from overlord_py.paths import build_workspace_paths


class FakeRunningEngine:
    name = "docker"

    def __init__(self):
        self.calls = []

    def run(self, args, *, cwd, env, input_text=None):
        argv = list(args)
        self.calls.append(argv)
        if argv[:2] == ["inspect", "--format"]:
            return CommandResult(argv=argv, returncode=0, stdout="running\n", stderr="")
        return CommandResult(argv=argv, returncode=0, stdout="", stderr="")


class ContainerLifecycleTests(unittest.TestCase):
    def test_running_container_repairs_root_owned_zsh_autocomplete_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            paths = build_workspace_paths(workspace, script_path=ROOT / "scripts" / "overlord")
            engine = FakeRunningEngine()

            result = ensure_running(engine, paths, (), env={})

        self.assertFalse(result.setup_ran)
        self.assertIn("/home/overlord/.local", SETUP_OWNERSHIP_REPAIR_SCRIPT)
        self.assertIn(
            ["exec", paths.identity.container_name, "sh", "-c", SETUP_OWNERSHIP_REPAIR_SCRIPT],
            engine.calls,
        )


if __name__ == "__main__":
    unittest.main()
