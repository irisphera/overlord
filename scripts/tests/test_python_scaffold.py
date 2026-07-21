from __future__ import annotations

import ast
import importlib
import py_compile
import sys
import unittest
from pathlib import Path
from typing import Final


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
PACKAGE_DIR: Final = SCRIPTS_DIR / "overlord_py"
REQUIRED_MODULES: Final = (
    "main",
    "cli",
    "paths",
    "engine",
    "config_catalog",
    "env_builder",
    "state",
    "container_lifecycle",
    "runtime_config",
    "packages",
    "web_server",
    "terminal",
    "errors",
)
LOCAL_ROOTS: Final = {"overlord_py"}
FORBIDDEN_EXAMPLES: Final = ("click", "requests")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class PythonScaffoldTests(unittest.TestCase):
    def test_required_scaffold_modules_import(self) -> None:
        for module in REQUIRED_MODULES:
            with self.subTest(module=module):
                imported = importlib.import_module(f"overlord_py.{module}")
                self.assertEqual(imported.__name__, f"overlord_py.{module}")

    def test_required_scaffold_modules_compile(self) -> None:
        for source_path in sorted(PACKAGE_DIR.glob("*.py")):
            with self.subTest(source=source_path.name):
                py_compile.compile(str(source_path), doraise=True)

    def test_package_exports_required_modules(self) -> None:
        package = importlib.import_module("overlord_py")

        self.assertEqual(package.__all__, REQUIRED_MODULES)

    def test_scaffold_sources_use_only_stdlib_or_local_imports(self) -> None:
        violations = stdlib_import_violations(PACKAGE_DIR)

        self.assertEqual(violations, [])

    def test_import_scanner_rejects_explicit_non_stdlib_examples(self) -> None:
        source = "import click\nfrom requests import Session\nfrom overlord_py import cli\n"

        self.assertEqual(import_violations_for_source(source), ["click", "requests"])


def stdlib_import_violations(package_dir: Path) -> list[str]:
    violations: list[str] = []
    for source_path in sorted(package_dir.glob("*.py")):
        for imported_root in import_roots(source_path.read_text(encoding="utf-8")):
            if is_allowed_import(imported_root):
                continue
            violations.append(f"{source_path.name}: {imported_root}")
    return violations


def import_violations_for_source(source: str) -> list[str]:
    return [imported_root for imported_root in import_roots(source) if not is_allowed_import(imported_root)]


def import_roots(source: str) -> list[str]:
    roots: list[str] = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Import(names=names):
                roots.extend(alias.name.partition(".")[0] for alias in names)
            case ast.ImportFrom(level=level, module=module):
                if level > 0 or module is None:
                    continue
                roots.append(module.partition(".")[0])
            case _:
                continue
    return sorted(set(roots))


def is_allowed_import(imported_root: str) -> bool:
    if imported_root in FORBIDDEN_EXAMPLES:
        return False
    if imported_root in LOCAL_ROOTS:
        return True
    if imported_root in sys.builtin_module_names:
        return True
    if imported_root in sys.stdlib_module_names:
        return True
    return False


if __name__ == "__main__":
    unittest.main()
