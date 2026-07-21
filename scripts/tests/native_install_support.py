from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple

from scripts.tests.harness import HarnessRun, TempLauncherWorkspace


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
INSTALLER: Final = REPO_ROOT / "scripts" / "install"
TOOL_VERSIONS_MANIFEST: Final = REPO_ROOT / "config" / "tool-versions.env"


class ToolVersions(NamedTuple):
    opencode: str
    oh_my_openagent: str
    codegraph: str
    rtk: str
    rtk_amd64_sha256: str
    rtk_arm64_sha256: str


@dataclass(frozen=True, slots=True)
class RtkInstallFixture:
    architecture: str = "x86_64"
    checksum_status: int = 0
    extracts_binary: bool = True
    reported_version: str | None = None
    creates_plugin: bool = True


DEFAULT_RTK_INSTALL_FIXTURE: Final = RtkInstallFixture()


def native_workspace() -> TempLauncherWorkspace:
    return TempLauncherWorkspace(prefix="overlord native install ")


def run_install(workspace: TempLauncherWorkspace, *args: str) -> HarnessRun:
    home = workspace.path / "home"
    return workspace.run_command((str(INSTALLER), *args), env=isolated_env(home))


def isolated_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "BUN_INSTALL": str(home / ".bun"),
    }


def install_fake_package_commands(
    workspace: TempLauncherWorkspace,
    versions: ToolVersions,
    *,
    rtk: RtkInstallFixture = DEFAULT_RTK_INSTALL_FIXTURE,
) -> None:
    _ = workspace.write_executable_in_fake_bin("bun", fake_bun_script(versions))
    _ = workspace.write_executable_in_fake_bin("node", fake_node_script(versions))
    _ = workspace.write_executable_in_fake_bin("zellij", "#!/usr/bin/env bash\nexit 0\n")
    _ = workspace.write_executable_in_fake_bin(
        "uname",
        f'#!/usr/bin/env bash\ncase "$1" in -s) printf "Linux\\n" ;; -m) printf "{rtk.architecture}\\n" ;; *) exit 1 ;; esac\n',
    )
    _ = workspace.write_executable_in_fake_bin("curl", fake_curl_script())
    _ = workspace.write_executable_in_fake_bin("sha256sum", fake_sha256sum_script(rtk.checksum_status))
    _ = workspace.write_executable_in_fake_bin("tar", fake_tar_script(versions, rtk))


def fake_bun_script(versions: ToolVersions) -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "printf 'FAKE_BUN %s\\n' \"$*\"",
            "case \"$*\" in",
            '"init -y")',
            "\tprintf '{\"private\":true}\\n' > package.json",
            "\t;;",
            f'"add -g opencode-ai@{versions.opencode}")',
            "\tmkdir -p \"${BUN_INSTALL}/bin\"",
            "\tprintf '#!/usr/bin/env bash\\nprintf '\"'\"'1.2.3\\\\n'\"'\"'\\n' > \"${BUN_INSTALL}/bin/opencode\"",
            "\tchmod +x \"${BUN_INSTALL}/bin/opencode\"",
            "\t;;",
            f'"add oh-my-openagent@{versions.oh_my_openagent} --safe-chain-skip-minimum-package-age")',
            "\tmkdir -p node_modules/oh-my-openagent/bin node_modules/.bin",
            f"\tprintf '{{\"version\":\"{versions.oh_my_openagent}\"}}\\n' > node_modules/oh-my-openagent/package.json",
            "\tprintf '#!/usr/bin/env bash\\nexit 0\\n' > node_modules/.bin/oh-my-openagent",
            "\tchmod +x node_modules/.bin/oh-my-openagent",
            "\t;;",
            f'"add -g @colbymchenry/codegraph@{versions.codegraph}")',
            "\tmkdir -p \"${BUN_INSTALL}/bin\"",
            "\tprintf '#!/usr/bin/env bash\\nexit 0\\n' > \"${BUN_INSTALL}/bin/codegraph\"",
            "\tchmod +x \"${BUN_INSTALL}/bin/codegraph\"",
            "\t;;",
            "*)",
            "\tprintf 'unexpected bun args: %s\\n' \"$*\" >&2",
            "\texit 99",
            "\t;;",
            "esac",
            "",
        )
    )


def fake_node_script(versions: ToolVersions) -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "case \"$*\" in",
            "*-p*oh-my-openagent*)",
            f"\tprintf '{versions.oh_my_openagent}\\n'",
            "\t;;",
            "*-p*codegraph*)",
            f"\tprintf '{versions.codegraph}\\n'",
            "\t;;",
            "*)",
            "\tprintf 'unexpected node args: %s\\n' \"$*\" >&2",
            "\texit 98",
            "\t;;",
            "esac",
            "",
        )
    )


def fake_curl_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
printf 'FAKE_CURL %s\n' "$*"
while [[ $# -gt 0 ]]; do
	case "$1" in
	-o) : >"$2"; shift 2 ;;
	*) shift ;;
	esac
done
"""


def fake_sha256sum_script(status: int) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
read -r checksum archive
printf 'FAKE_SHA256SUM %s %s\n' "${{checksum}}" "${{archive}}"
exit {status}
"""


def fake_tar_script(versions: ToolVersions, rtk: RtkInstallFixture) -> str:
    if not rtk.extracts_binary:
        return "#!/usr/bin/env bash\nset -euo pipefail\n"
    reported_version = f"rtk {versions.rtk}" if rtk.reported_version is None else rtk.reported_version
    plugin_install = (
        '\tmkdir -p "${HOME}/.config/opencode/plugins"\n\tprintf \'export const RtkPlugin = true;\\n\' >"${HOME}/.config/opencode/plugins/rtk.ts"'
        if rtk.creates_plugin
        else "\t:"
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
	case "$1" in
	-C) destination="$2"; shift 2 ;;
	*) shift ;;
	esac
done
cat >"${{destination}}/rtk" <<'RTK'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
--version) printf '{reported_version}\\n' ;;
'init --global --opencode')
{plugin_install}
	;;
*) exit 97 ;;
esac
RTK
chmod +x "${{destination}}/rtk"
"""


def load_tool_versions() -> ToolVersions:
    result = subprocess.run(
        (
            "bash",
            "-c",
            'set -euo pipefail; . "$1"; printf "%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n" "$OPENCODE_VERSION" "$OH_MY_OPENAGENT_VERSION" "$CODEGRAPH_VERSION" "$RTK_VERSION" "$RTK_AMD64_SHA256" "$RTK_ARM64_SHA256"',
            "bash",
            str(TOOL_VERSIONS_MANIFEST),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    opencode, oh_my_openagent, codegraph, rtk, rtk_amd64_sha256, rtk_arm64_sha256 = result.stdout.splitlines()
    return ToolVersions(opencode, oh_my_openagent, codegraph, rtk, rtk_amd64_sha256, rtk_arm64_sha256)
