import re
import unittest
from collections import defaultdict
from pathlib import Path

CONFIG = Path("config/zellij-config.kdl")

# All zellij input modes that keybind blocks can target.
MODES = [
    "normal", "locked", "pane", "tab", "resize", "move", "scroll", "search",
    "session", "tmux", "entersearch", "renametab", "renamepane",
]


def parse_blocks(body):
    """Yield (kind, modes, inner) for each block inside the keybinds section."""
    out = []
    idx = 0
    while True:
        m = re.search(
            r'(?:(shared_among|shared_except)\s+((?:"[a-z]+"\s*)+))|([a-z]+)\s*\{',
            body[idx:],
        )
        if not m:
            break
        start = idx + m.end() - 1  # position of the opening brace
        kind = m.group(1)
        modes = re.findall(r'"([a-z]+)"', m.group(2) or "")
        name = m.group(3)
        depth = 0
        j = start
        while j < len(body):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((kind or "single", modes if kind else [name], body[start + 1 : j]))
        idx = j + 1
    return out


def effective_binds(src):
    """Map mode -> key -> [actions] after resolving shared_* scopes."""
    body = src.split("keybinds clear-defaults=true {", 1)[1]
    # cut everything after the closing brace of the keybinds section;
    # its opening brace is already consumed by the split, so start at depth 1
    depth = 1
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body = body[:i]
                break
    result = {m: defaultdict(list) for m in MODES}
    for kind, modes, inner in parse_blocks(body):
        binds = re.findall(r'bind\s+"([^"]+)"\s*\{([^{}]*)\}', inner)
        if kind == "shared_among":
            targets = modes
        elif kind == "shared_except":
            targets = [m for m in MODES if m not in modes]
        else:
            targets = modes
        for key, action in binds:
            for mode in targets:
                result[mode][key].append(re.sub(r"\s+", " ", action.strip()).rstrip(";"))
    return result


class ZellijConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONFIG.exists(), "config/zellij-config.kdl must exist")
        self.src = CONFIG.read_text(encoding="utf-8")
        self.binds = effective_binds(self.src)

    def test_no_duplicate_key_within_any_mode(self):
        """Every (mode, key) pair must map to at most one binding."""
        conflicts = []
        for mode in MODES:
            for key, actions in self.binds[mode].items():
                unique = set(actions)
                if len(actions) > 1 and len(unique) > 1:
                    conflicts.append((mode, key, sorted(unique)))
        self.assertEqual(conflicts, [], f"conflicting keybinds found: {conflicts}")

    def test_tab_mode_on_ctrl_b(self):
        """Tab mode must be entered with Ctrl+b from normal mode."""
        entries = self.binds["normal"].get("Ctrl b", [])
        self.assertIn('SwitchToMode "tab"', entries)

    def test_ctrl_t_left_unbound_for_app_passthrough(self):
        """Ctrl+t must stay unbound so terminal apps receive it."""
        for mode in MODES:
            self.assertNotIn("Ctrl t", self.binds[mode], f"Ctrl t bound in {mode}")

    def test_tmux_mode_removed(self):
        """The tmux mode must not be targeted by any shared scope or block."""
        body = self.src
        self.assertNotIn('shared_among "tmux"', body)
        self.assertNotIn('shared_including "tmux"', body)


if __name__ == "__main__":
    unittest.main()
