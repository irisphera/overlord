import unittest
from pathlib import Path

class SetupShTests(unittest.TestCase):
    def test_setup_sh_exists_and_non_interactive(self):
        p = Path("setup.sh")
        self.assertTrue(p.exists(), "setup.sh must exist")
        content = p.read_text(encoding="utf-8")
        # must be non-interactive
        self.assertIn("DEBIAN_FRONTEND=noninteractive", content)
        self.assertIn("zsh", content.lower())
        self.assertIn("zsh-autosuggestions", content)
        self.assertIn("zsh-syntax-highlighting", content)
        self.assertIn("zsh-completions", content)
        self.assertIn("zellij", content.lower())
        self.assertIn("lazyvim", content.lower())
        # must handle sudo passwordless
        self.assertIn("NOPASSWD", content)
        self.assertIn("sudo -n true", content)
        # must be idempotent (uses --unattended, --depth=1, exists checks)
        self.assertIn("--unattended", content)
        # no old opencode install (allow prime-agent provider names like opencode/opencode-go)
        self.assertNotIn("opencode-ai", content.lower())
        self.assertNotIn("oh-my-openagent@", content.lower())
        # ensure prime-agent models.json generation is present
        self.assertIn("prime-agent", content.lower())
        self.assertIn("models.json", content)
        self.assertIn("272000", content)
        # ensure codegraph is installed and skill is wired
        self.assertIn("codegraph", content.lower())
        self.assertIn("CODEGRAPH_VERSION", content)

    def test_setup_sh_executable(self):
        import os, stat
        p = Path("setup.sh")
        self.assertTrue(bool(p.stat().st_mode & stat.S_IXUSR))

    def test_dockerfile_no_opencode(self):
        content = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("opencode-ai", content.lower())
        self.assertNotIn("oh-my-openagent@", content.lower())
        self.assertIn("setup.sh", content)
        self.assertIn("codegraph", content.lower())

    def test_codegraph_skill_exists(self):
        self.assertTrue(Path("skills/codegraph/SKILL.md").exists())
        self.assertTrue(Path(".prime/agent/skills/codegraph/SKILL.md").exists())
        self.assertIn("codegraph", Path("skills/codegraph/SKILL.md").read_text().lower())

if __name__ == "__main__":
    unittest.main()
