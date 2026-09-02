import json
import os
import subprocess
import tempfile
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
        self.assertIn("256000", content)
        self.assertIn('gpt-5.6-luna', content)
        # The setup block constructs the context suffix dynamically.
        self.assertIn('"GPT-5.6 Luna"', content)
        self.assertIn('context_label', content)
        self.assertIn('thinkingLevelMap', content)
        self.assertIn('"max": "max"', content)
        # Bind mounts can expose the persisted file through multiple path names.
        # The copy guard must compare file identity, not path text, to avoid cp
        # rejecting a copy of models.json onto itself.
        self.assertIn('[ ! "$models_source" -ef "$d/models.json" ]', content)
        # ensure codegraph is installed and skill is wired
        self.assertIn("codegraph", content.lower())
        self.assertIn("CODEGRAPH_VERSION", content)

    def test_fresh_models_generation_removes_opencode_custom_models(self):
        content = Path("setup.sh").read_text(encoding="utf-8")
        marker = 'python3 - "$tmp_json" <<\'PYEOF\'\n'
        start = content.index(marker) + len(marker)
        script = content[start : content.index("\nPYEOF", start)]
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_prime = fake_bin / "prime-agent"
            fake_prime.write_text("#!/bin/sh\nexit 0\n")
            fake_prime.chmod(0o755)
            output = Path(tmp) / "models.json"
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            completed = subprocess.run(
                ["python3", "-", str(output)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(output.read_text())
            self.assertNotIn("opencode", data["providers"])
            self.assertEqual(
                {model["id"] for model in data["providers"]["opencode-go"]["models"]},
                {"gpt-5.6-luna", "muse-spark-1.2-contributor"},
            )
            self.assertNotIn("muse-spark-1.2-free", json.dumps(data))
            self.assertNotIn("muse-spark-1.2-contributor-free", json.dumps(data))

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
