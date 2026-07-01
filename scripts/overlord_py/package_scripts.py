from __future__ import annotations

from typing import Final

OPENCODE_INSTALL_SCRIPT: Final = r'''set -e

required_version="$1"
install_log="$(mktemp)"
bun_bin="/usr/local/bin/bun"

if [ ! -x "${bun_bin}" ]; then
	bun_bin="bun"
fi

cleanup() {
	rm -f "${install_log}"
}

trap cleanup EXIT

if ! "${bun_bin}" add -g "opencode-ai@${required_version}" >"${install_log}" 2>&1; then
	cat "${install_log}" >&2
	exit 1
fi
'''

OH_MY_OPENAGENT_CHECK_SCRIPT: Final = r'''set -e

required_version="$1"
cache_dir="$2"
public_bin="$3"
package_dir="${cache_dir}/node_modules/oh-my-openagent"
package_json="${package_dir}/package.json"
canonical_bin="${cache_dir}/node_modules/.bin/oh-my-openagent"
fallback_bin="${package_dir}/bin/oh-my-opencode.js"

if [ ! -f "${package_json}" ] || [ ! -x "${public_bin}" ]; then
	exit 1
fi

installed_version="$(node -p "require('${package_json}').version" 2>/dev/null || true)"
if [ "${required_version}" = "latest" ]; then
	latest_version="$(npm view oh-my-openagent version 2>/dev/null || true)"
	if [ -n "${latest_version}" ]; then
		test "${installed_version}" = "${latest_version}"
	else
		test -n "${installed_version}"
	fi
else
	test "${installed_version}" = "${required_version}"
fi
'''

OH_MY_OPENAGENT_INSTALL_SCRIPT: Final = r'''set -e

package_spec="$1"
cache_dir="$2"
public_bin="$3"
package_dir="${cache_dir}/node_modules/oh-my-openagent"
package_json="${package_dir}/package.json"
canonical_bin="${cache_dir}/node_modules/.bin/oh-my-openagent"
fallback_bin="${package_dir}/bin/oh-my-opencode.js"
install_log="$(mktemp)"
bun_bin="/usr/local/bin/bun"

if [ ! -x "${bun_bin}" ]; then
	bun_bin="bun"
fi

cleanup() {
	rm -f "${install_log}"
}

trap cleanup EXIT

mkdir -p "${cache_dir}" "$(dirname "${public_bin}")"
cd "${cache_dir}"

if [ ! -f package.json ]; then
	"${bun_bin}" init -y >/dev/null 2>&1
fi

if ! "${bun_bin}" add "${package_spec}" --safe-chain-skip-minimum-package-age >"${install_log}" 2>&1; then
	cat "${install_log}" >&2
	exit 1
fi

if [ -x "${canonical_bin}" ]; then
	ln -sf "${canonical_bin}" "${public_bin}"
elif [ -x "${fallback_bin}" ]; then
	ln -sf "${fallback_bin}" "${public_bin}"
else
	echo "Error: ${package_spec} installed without an executable oh-my-openagent entrypoint" >&2
	exit 1
fi

test -x "${public_bin}"
'''

CODEGRAPH_CHECK_SCRIPT: Final = r'''set -e

required_version="$1"
public_bin="$2"
if [ ! -x "${public_bin}" ]; then
	exit 1
fi
installed_version="$(node -p "require('/home/overlord/.bun/install/global/node_modules/@colbymchenry/codegraph/package.json').version" 2>/dev/null || true)"
test "${installed_version}" = "${required_version}"
'''

CODEGRAPH_INSTALL_SCRIPT: Final = r'''set -e

package_spec="$1"
public_bin="$2"
bun_bin="/usr/local/bin/bun"
install_log="$(mktemp)"

if [ ! -x "${bun_bin}" ]; then
	bun_bin="bun"
fi

cleanup() {
	rm -f "${install_log}"
}

trap cleanup EXIT

mkdir -p "$(dirname "${public_bin}")"
if ! "${bun_bin}" add -g "${package_spec}" >"${install_log}" 2>&1; then
	cat "${install_log}" >&2
	exit 1
fi

if [ ! -x /home/overlord/.bun/bin/codegraph ]; then
	echo "Error: ${package_spec} installed without an executable codegraph entrypoint" >&2
	exit 1
fi

ln -sf /home/overlord/.bun/bin/codegraph "${public_bin}"
test -x "${public_bin}"
'''

DEFAULT_SKILLS_CHECK_SCRIPT: Final = r'''set -e

for marker in "$@"; do
	test -f "${marker}" || exit 1
done
'''

DEFAULT_SKILLS_INSTALL_SCRIPT: Final = r'''set -e

npx_package="$1"
source="$2"
install_log="$(mktemp)"

cleanup() {
	rm -f "${install_log}"
}

trap cleanup EXIT

if ! DISABLE_TELEMETRY=1 npx --yes "${npx_package}" add "${source}" --skill '*' --agent opencode --global --yes --copy >"${install_log}" 2>&1; then
	cat "${install_log}" >&2
	exit 1
fi

test -f /home/overlord/.agents/skills/setup-matt-pocock-skills/SKILL.md
test -f /home/overlord/.agents/skills/tdd/SKILL.md
'''
