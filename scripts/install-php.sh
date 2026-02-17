#!/bin/bash
# Install PHP and Intelephense Language Server for OpenCode LSP support
set -e

echo "==> Installing PHP and Intelephense Language Server..."

install_php_debian() {
    echo "Installing PHP via apt..."
    sudo apt-get update
    sudo apt-get install -y php php-cli php-mbstring php-xml php-curl php-zip
}

install_php_macos() {
    echo "Installing PHP via Homebrew..."
    brew install php
}

if ! command -v php &> /dev/null; then
    echo "PHP not found. Installing..."
    
    if [ -f /etc/debian_version ]; then
        install_php_debian
    elif [ "$(uname -s)" = "Darwin" ]; then
        install_php_macos
    else
        echo "Error: Unsupported OS. Please install PHP manually." >&2
        exit 1
    fi
fi

PHP_VERSION=$(php -v | head -1)
echo "PHP installed: $PHP_VERSION"

echo "==> Installing Intelephense Language Server..."

if command -v bun &> /dev/null; then
    PKG_MGR="bun"
    GLOBAL_FLAG="add -g"
elif command -v npm &> /dev/null; then
    PKG_MGR="npm"
    GLOBAL_FLAG="install -g"
else
    echo "Error: Neither bun nor npm found. Please install Node.js or Bun first." >&2
    exit 1
fi

echo "Using package manager: $PKG_MGR"

$PKG_MGR $GLOBAL_FLAG intelephense

INTELEPHENSE_PATH=$(which intelephense 2>/dev/null || echo "not found")

echo ""
echo "==> Installation complete!"
echo "    PHP:          $PHP_VERSION"
echo "    Intelephense: $INTELEPHENSE_PATH"
echo ""
echo "OpenCode should now detect and use the PHP language server."
echo ""
echo "Note: For full Intelephense features, consider purchasing a license key"
echo "      and setting INTELEPHENSE_LICENSE_KEY environment variable."
