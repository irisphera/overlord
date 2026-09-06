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
                {"gpt-5.6-luna", "muse-spark-1.3-contributor"},
            )
            self.assertEqual(
                {model["id"] for model in data["providers"]["google-vertex"]["models"]},
                {"gemini-3.8-flash"},
            )
            serialized = json.dumps(data)
            self.assertNotIn("gemini-3.7-flash", serialized)
            self.assertNotIn("muse-spark-1.2", serialized)
            self.assertNotIn("muse-spark-1.3-free", serialized)
            self.assertNotIn("muse-spark-1.3-contributor-free", serialized)
            # Both opencode-go models support max thinking.
            for mid in ("gpt-5.6-luna", "muse-spark-1.3-contributor"):
                self.assertEqual(data["providers"]["opencode-go"]["modelOverrides"][mid].get("thinkingLevelMap", {}).get("max"), "max")
            muse_models = {m["id"]: m for m in data["providers"]["opencode-go"]["models"]}
            self.assertEqual(muse_models["muse-spark-1.3-contributor"].get("thinkingLevelMap", {}).get("max"), "max")

    def test_omp_state_ownership_repairs_foreign_descendants(self):
        content = Path("setup.sh").read_text(encoding="utf-8")
        start = content.index("own_provisioned_home_files() {")
        end = content.index("\n}\n", start) + len("\n}")
        helper = content[start:end]
        # Mock ownership checks, not the helper. No real chown or sudo is used,
        # so this covers root-owned leftovers even when tests run unprivileged.
        mocks = r'''
stat() { printf '%s\n' testuser; }
getent() { return 0; }
id() { printf '%s\n' 1000; }
find() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$FIND_LOG"
  if [ -n "$FOREIGN_PATH" ] && [[ "$FOREIGN_PATH" == "$1/"* ]]; then
    printf '%s\n' "$FOREIGN_PATH"
  fi
}
chown() { printf '%s\t%s\t%s\n' "$@" >> "$CHOWN_LOG"; }
run_sudo() { printf 'unexpected sudo\n' >> "$CHOWN_LOG"; return 1; }
warn() { printf '%s\n' "$*" >&2; }
'''
        for foreign in ("agent/models.json", "natives/addon.node", None):
            with self.subTest(foreign=foreign), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "test home"
                omp = home / ".omp"
                (omp / "agent").mkdir(parents=True)
                (omp / "natives").mkdir()
                (omp / "agent/models.json").write_text("{}")
                (omp / "natives/addon.node").touch()
                find_log = Path(tmp) / "find.log"
                chown_log = Path(tmp) / "chown.log"
                find_log.touch()
                chown_log.touch()
                env = dict(os.environ)
                env.update(
                    TEST_HOME=str(home),
                    FOREIGN_PATH=str(omp / foreign) if foreign else "",
                    FIND_LOG=str(find_log),
                    CHOWN_LOG=str(chown_log),
                )
                completed = subprocess.run(
                    ["bash", "-c", mocks + helper + '\nown_provisioned_home_files "$TEST_HOME"\n'],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    find_log.read_text().splitlines(),
                    [f"{omp}\t-not\t-user\ttestuser\t-print\t-quit"],
                )
                self.assertEqual(
                    chown_log.read_text().splitlines(),
                    [f"-R\ttestuser:testuser\t{omp}"] if foreign else [],
                )

    def test_final_home_ownership_repair_follows_configuration(self):
        lines = Path("setup.sh").read_text(encoding="utf-8").splitlines()
        # Match top-level calls only, not function definitions or comments.
        repair = max(i for i, line in enumerate(lines) if line == "own_all_provisioned_homes")
        for configure in (
            "install_prime_agent_skills",
            "configure_prime_agent_models",
            "configure_omp_models",
            "configure_codex",
        ):
            self.assertGreater(repair, lines.index(configure), configure)
        self.assertLess(repair, lines.index("make_zsh_default"))

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
