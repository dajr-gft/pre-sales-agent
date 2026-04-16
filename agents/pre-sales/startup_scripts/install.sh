#!/bin/bash
set -euo pipefail

echo "=============================================="
echo "Startup script: installing D2 and rsvg-convert deps"
echo "=============================================="

apt-get update -qq
apt-get install -y --no-install-recommends -qq \
    curl \
    librsvg2-bin \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig

D2_VERSION="v0.7.1"
D2_TARBALL="d2-${D2_VERSION}-linux-amd64.tar.gz"
D2_URL="https://github.com/terrastruct/d2/releases/download/${D2_VERSION}/${D2_TARBALL}"
EXPECTED_SHA256="eb172adf59f38d1e5a70ab177591356754ffaf9bebb84e0ca8b767dfb421dad7"

echo "Downloading D2 ${D2_VERSION}..."
curl -fsSL -o /tmp/d2.tar.gz "$D2_URL"

echo "Verifying SHA256 checksum..."
ACTUAL_SHA256=$(sha256sum /tmp/d2.tar.gz | awk '{print $1}')
if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "ERROR: SHA256 mismatch!"
    echo "  Expected: $EXPECTED_SHA256"
    echo "  Got:      $ACTUAL_SHA256"
    rm -f /tmp/d2.tar.gz
    exit 1
fi
echo "Checksum OK."

echo "Extracting D2..."
mkdir -p /tmp/d2-extract
tar -xzf /tmp/d2.tar.gz -C /tmp/d2-extract

echo "Installing D2 binary to /usr/local/bin..."
cp "/tmp/d2-extract/d2-${D2_VERSION}/bin/d2" /usr/local/bin/d2
chmod +x /usr/local/bin/d2

rm -rf /tmp/d2.tar.gz /tmp/d2-extract
apt-get clean
rm -rf /var/lib/apt/lists/*

if [ -x /usr/local/bin/d2 ]; then
    echo "D2 installed at: /usr/local/bin/d2"
    echo "D2 version: $(/usr/local/bin/d2 --version 2>&1)"
else
    echo "ERROR: D2 binary not found at /usr/local/bin/d2 after install"
    exit 1
fi

echo "=============================================="
echo "Startup script: DONE"
echo "=============================================="