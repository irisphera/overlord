import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import unittest
from pathlib import Path
from overlord_py.tool_versions import load_tool_versions

class ToolVersionsTests(unittest.TestCase):
    def test_load(self):
        tv = load_tool_versions()
        self.assertRegex(tv.zellij_version, r"\d+\.\d+\.\d+")
        self.assertRegex(tv.codex_version, r"\d+\.\d+\.\d+")
if __name__ == "__main__":
    unittest.main()
