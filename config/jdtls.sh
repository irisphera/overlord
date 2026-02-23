#!/bin/bash
# JDTLS wrapper — launches Eclipse JDT Language Server with Lombok support
# Used by editors/LSP clients as the Java language server command

set -euo pipefail

JDTLS_HOME="${JDTLS_HOME:-/opt/jdtls}"
JAVA_HOME="${JAVA_HOME:-/opt/jdk-24}"
LOMBOK_JAR="/opt/lombok.jar"
DATA_DIR="${JDTLS_DATA_DIR:-${HOME}/.cache/jdtls-workspace}"

# Detect platform config
if [ "$(uname -m)" = "aarch64" ]; then
    CONFIG_DIR="${JDTLS_HOME}/config_linux_arm"
else
    CONFIG_DIR="${JDTLS_HOME}/config_linux"
fi

# Find the launcher jar
LAUNCHER_JAR=$(find "${JDTLS_HOME}/plugins" -name 'org.eclipse.equinox.launcher_*.jar' | head -1)

if [ -z "${LAUNCHER_JAR}" ]; then
    echo "ERROR: Could not find JDTLS launcher jar in ${JDTLS_HOME}/plugins" >&2
    exit 1
fi

exec "${JAVA_HOME}/bin/java" \
    -javaagent:"${LOMBOK_JAR}" \
    -Declipse.application=org.eclipse.jdt.ls.core.id1 \
    -Dosgi.bundles.defaultStartLevel=4 \
    -Declipse.product=org.eclipse.jdt.ls.core.product \
    -Dlog.level=ALL \
    -Xmx1G \
    --add-modules=ALL-SYSTEM \
    --add-opens java.base/java.util=ALL-UNNAMED \
    --add-opens java.base/java.lang=ALL-UNNAMED \
    -jar "${LAUNCHER_JAR}" \
    -configuration "${CONFIG_DIR}" \
    -data "${DATA_DIR}" \
    "$@"
