from __future__ import annotations

import unittest
from pathlib import Path
from typing import Final


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
AUTHORED_ROOTS: Final = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "Dockerfile",
    REPO_ROOT / "README.md",
    REPO_ROOT / "config",
    REPO_ROOT / "scripts",
    REPO_ROOT / "skills",
)
REMOVED_PRODUCT: Final = "".join(("head", "room"))


class RepositoryHygieneTests(unittest.TestCase):
    def test_removed_product_name_is_absent_from_authored_files(self) -> None:
        matches: list[str] = []

        for path in authored_files():
            try:
                contents = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if REMOVED_PRODUCT in contents.lower():
                matches.append(str(path.relative_to(REPO_ROOT)))

        self.assertEqual(matches, [])


def authored_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in AUTHORED_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return tuple(files)


if __name__ == "__main__":
    _ = unittest.main()
