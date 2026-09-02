import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import unittest
from pathlib import Path

from overlord_py.env_builder import build_environment_plan


class EnvironmentBuilderTests(unittest.TestCase):
    def test_forwards_opencode_key_to_container(self):
        plan = build_environment_plan(
            {"OPENCODE_API_KEY": "test-key"},
            home=Path("/tmp/home"),
            workspace_name="workspace",
        )

        self.assertIn("OPENCODE_API_KEY=test-key", plan.exec_env_values)
        self.assertIn("OPENCODE_API_KEY=test-key", plan.exec_env_flags)

    def test_omits_empty_opencode_key(self):
        plan = build_environment_plan(
            {"OPENCODE_API_KEY": ""},
            home=Path("/tmp/home"),
            workspace_name="workspace",
        )

        self.assertNotIn("OPENCODE_API_KEY=", plan.exec_env_values)
        self.assertNotIn("OPENCODE_API_KEY=", plan.exec_env_flags)


if __name__ == "__main__":
    unittest.main()
