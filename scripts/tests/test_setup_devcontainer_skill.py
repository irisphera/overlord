from __future__ import annotations

import unittest
from pathlib import Path
from typing import Final


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SKILL_FILE: Final = REPO_ROOT / "skills" / "setup-devcontainer" / "SKILL.md"


class SetupDevcontainerSkillContractTests(unittest.TestCase):
    def test_repository_skill_has_complete_valid_frontmatter(self) -> None:
        self.assertTrue(SKILL_FILE.is_file(), f"Missing repository skill: {SKILL_FILE}")
        lines = SKILL_FILE.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines[0], "---")
        closing_delimiter = lines.index("---", 1)
        frontmatter: dict[str, str] = {}
        for line in lines[1:closing_delimiter]:
            key, separator, value = line.partition(":")
            self.assertEqual(separator, ":", f"Malformed frontmatter line: {line}")
            self.assertNotIn(key, frontmatter, f"Duplicate frontmatter key: {key}")
            frontmatter[key] = value.strip()

        self.assertEqual(set(frontmatter), {"name", "description", "compatibility"})
        self.assertEqual(frontmatter["name"], "setup-devcontainer")
        self.assertEqual(frontmatter["compatibility"], "opencode")
        self.assertTrue(frontmatter["description"].startswith("Create or safely update setup-devcontainer.sh"))
        self.assertIn("runtimes", frontmatter["description"])
        self.assertIn("language servers", frontmatter["description"])

    def test_repository_skill_declares_complete_setup_contract(self) -> None:
        content = SKILL_FILE.read_text(encoding="utf-8")
        sections: dict[str, str] = {}
        current_heading = ""
        for line in content.splitlines():
            if line.startswith("## "):
                current_heading = line.removeprefix("## ")
                sections[current_heading] = ""
            elif current_heading:
                sections[current_heading] += f"{line}\n"

        self.assertEqual(set(sections), {"1. Inspect before editing", "2. Plan the script", "3. Create or update", "4. Verify"})

        inspect = sections["1. Inspect before editing"].lower()
        self.assertIn("active project root", inspect)
        self.assertIn("git rev-parse --show-toplevel", inspect)
        self.assertIn("manifests", inspect)
        self.assertIn("configuration", inspect)
        self.assertIn("runtime", inspect)
        self.assertIn("build tool", inspect)
        self.assertIn("language server", inspect)
        self.assertIn("do not guess", inspect)

        plan = sections["2. Plan the script"]
        self.assertIn("deterministic and idempotent", plan)
        self.assertIn("Require root explicitly and `cd /workspace`", plan)
        self.assertIn("Never pipe network responses into a shell or interpreter", plan)
        self.assertIn("evidence-backed pinned version", plan)
        self.assertIn("verify its integrity", plan)
        self.assertIn("unverified remote installer", plan)
        self.assertIn("explicit user approval", plan)

        create_or_update = sections["3. Create or update"]
        self.assertIn("If `$PROJECT_ROOT/setup-devcontainer.sh` does not exist, create it", create_or_update)
        self.assertIn("If `$PROJECT_ROOT/setup-devcontainer.sh` already exists, update it", create_or_update)
        self.assertIn("Preserve unrelated commands", create_or_update)
        self.assertIn("preservation rules take precedence", create_or_update)
        self.assertIn("chmod 755 \"$PROJECT_ROOT/setup-devcontainer.sh\"", create_or_update)

        verify = sections["4. Verify"]
        self.assertIn('bash -n "$PROJECT_ROOT/setup-devcontainer.sh"', verify)
        self.assertIn('shellcheck "$PROJECT_ROOT/setup-devcontainer.sh"', verify)
        self.assertIn("When `shellcheck` is available", verify)
        self.assertIn("When `shfmt` is available", verify)


if __name__ == "__main__":
    _ = unittest.main()
