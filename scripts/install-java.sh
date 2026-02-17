#!/bin/bash
# Install Java 25 and Eclipse JDT Language Server for OpenCode LSP support
set -e

echo "==> Installing Java 25 (OpenJDK EA) and Eclipse JDT Language Server..."

# Detect architecture
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "${ARCH}" in
    amd64|x86_64) JDK_ARCH="x64" ;;
    arm64|aarch64) JDK_ARCH="aarch64" ;;
    *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac

# Java 25 EA from Adoptium/Eclipse Temurin or Oracle
# Using SDKMAN for reliable Java version management
export SDKMAN_DIR="${SDKMAN_DIR:-$HOME/.sdkman}"

if [ ! -d "$SDKMAN_DIR" ]; then
    echo "==> Installing SDKMAN..."
    curl -fsSL "https://get.sdkman.io?rcupdate=false" | bash
fi

# Source SDKMAN
source "$SDKMAN_DIR/bin/sdkman-init.sh"

# Install Java 25 EA (early access) - using Oracle OpenJDK EA builds
echo "==> Installing Java 25 EA..."
sdk install java 25.ea-open || sdk install java 25-ea || {
    echo "Java 25 EA not available via SDKMAN, trying manual install..."
    
    # Fallback: Manual Oracle OpenJDK 25 EA install
    JDK_URL="https://download.java.net/java/early_access/jdk25/8/GPL/openjdk-25-ea+8_linux-${JDK_ARCH}_bin.tar.gz"
    JDK_DIR="$HOME/.local/jdk-25"
    
    mkdir -p "$HOME/.local"
    echo "Downloading OpenJDK 25 EA..."
    curl -fsSL "$JDK_URL" | tar xz -C "$HOME/.local"
    mv "$HOME/.local/jdk-25" "$JDK_DIR" 2>/dev/null || true
    
    # Set up alternatives
    export JAVA_HOME="$JDK_DIR"
    export PATH="$JAVA_HOME/bin:$PATH"
    
    # Add to shell profile
    echo "export JAVA_HOME=\"$JDK_DIR\"" >> "$HOME/.zshrc"
    echo "export PATH=\"\$JAVA_HOME/bin:\$PATH\"" >> "$HOME/.zshrc"
}

# Verify Java installation
java -version

# Install Eclipse JDT Language Server
echo "==> Installing Eclipse JDT Language Server..."

JDTLS_VERSION="1.43.0"
JDTLS_RELEASE="202501161338"
JDTLS_DIR="$HOME/.local/share/jdtls"
JDTLS_URL="https://download.eclipse.org/jdtls/milestones/${JDTLS_VERSION}/jdt-language-server-${JDTLS_VERSION}-${JDTLS_RELEASE}.tar.gz"

mkdir -p "$JDTLS_DIR"
echo "Downloading Eclipse JDT LS ${JDTLS_VERSION}..."
curl -fsSL "$JDTLS_URL" | tar xz -C "$JDTLS_DIR"

# Create launcher script
JDTLS_LAUNCHER="$HOME/.local/bin/jdtls"
mkdir -p "$HOME/.local/bin"

cat > "$JDTLS_LAUNCHER" << 'EOF'
#!/bin/bash
# Eclipse JDT Language Server launcher for OpenCode

JDTLS_HOME="${JDTLS_HOME:-$HOME/.local/share/jdtls}"
WORKSPACE_DIR="${1:-$HOME/.cache/jdtls-workspace}"

# Find the launcher jar
LAUNCHER_JAR=$(find "$JDTLS_HOME/plugins" -name 'org.eclipse.equinox.launcher_*.jar' | head -1)

if [ -z "$LAUNCHER_JAR" ]; then
    echo "Error: Could not find JDT LS launcher jar" >&2
    exit 1
fi

# Detect OS for config
case "$(uname -s)" in
    Linux*)  CONFIG_DIR="$JDTLS_HOME/config_linux" ;;
    Darwin*) CONFIG_DIR="$JDTLS_HOME/config_mac" ;;
    *)       CONFIG_DIR="$JDTLS_HOME/config_linux" ;;
esac

# Create workspace directory
mkdir -p "$WORKSPACE_DIR"

exec java \
    -Declipse.application=org.eclipse.jdt.ls.core.id1 \
    -Dosgi.bundles.defaultStartLevel=4 \
    -Declipse.product=org.eclipse.jdt.ls.core.product \
    -Dlog.level=ALL \
    -Xms256m \
    -Xmx2G \
    --add-modules=ALL-SYSTEM \
    --add-opens java.base/java.util=ALL-UNNAMED \
    --add-opens java.base/java.lang=ALL-UNNAMED \
    -jar "$LAUNCHER_JAR" \
    -configuration "$CONFIG_DIR" \
    -data "$WORKSPACE_DIR"
EOF

chmod +x "$JDTLS_LAUNCHER"

# Verify installation
echo ""
echo "==> Installation complete!"
echo "    Java:   $(java -version 2>&1 | head -1)"
echo "    JDT LS: $JDTLS_DIR"
echo "    Binary: $JDTLS_LAUNCHER"
echo ""
echo "Add to your shell profile if not already present:"
echo '    export PATH="$HOME/.local/bin:$PATH"'
echo ""
echo "OpenCode should now detect and use the Java language server."
