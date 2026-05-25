#!/bin/bash
# ============================================================
#  OpenPLC Runtime V4 — Raspberry Pi GPIO Plugin Installer
# ============================================================
#  Run this script on a Raspberry Pi that already has
#  OpenPLC Runtime V4 desktop installed:
#
#    curl -fsSL https://raw.githubusercontent.com/Microdesys/openplcV4-rpi-gpio/main/install.sh | sudo bash
#
#  GPIO mapping installed by this script:
#    Outputs: GPIO21=%QX0.0  GPIO20=%QX0.1  GPIO16=%QX0.2  GPIO12=%QX0.3
#    Inputs:  GPIO26=%IX0.0  GPIO19=%IX0.1  GPIO13=%IX0.2  GPIO5=%IX0.3
#             GPIO22=%IX0.4  GPIO27=%IX0.5  GPIO17=%IX0.6  GPIO4=%IX0.7
#  All inputs use external pull-down resistors (no internal pull configured).
# ============================================================

set -euo pipefail

# Replace with your actual GitHub username once the repo is created
GITHUB_RAW="https://raw.githubusercontent.com/Microdesys/openplcV4-rpi-gpio/main"

PLUGIN_NAME="rpi_gpio"
PLUGIN_FILE="rpi_gpio_plugin.py"

echo "============================================"
echo " OpenPLC V4 — Raspberry Pi GPIO Plugin"
echo "============================================"
echo ""

# ── Step 1: Verify the script is running as root ──────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)."
    exit 1
fi

# ── Step 2: Locate the OpenPLC V4 installation directory ─────────────────────
echo "[1/6] Locating OpenPLC Runtime V4 installation..."

# Read the WorkingDirectory from the systemd service unit
OPENPLC_DIR=$(systemctl cat openplc-runtime.service 2>/dev/null \
    | grep "^WorkingDirectory" | cut -d= -f2)

if [[ -z "$OPENPLC_DIR" || ! -d "$OPENPLC_DIR" ]]; then
    echo "ERROR: Could not find OpenPLC Runtime V4."
    echo "  Make sure openplc-runtime.service is installed and enabled."
    exit 1
fi

echo "  Found OpenPLC at: $OPENPLC_DIR"

# Derive all relevant paths from the installation directory
PLUGIN_DIR="$OPENPLC_DIR/core/src/drivers/plugins/python/$PLUGIN_NAME"
VENV_DIR="$OPENPLC_DIR/venvs/$PLUGIN_NAME"
PLUGINS_CONF="$OPENPLC_DIR/plugins.conf"

# ── Step 3: Download plugin files from GitHub ────────────────────────────────
echo "[2/6] Downloading plugin files..."

mkdir -p "$PLUGIN_DIR"

curl -fsSL "$GITHUB_RAW/rpi_gpio/$PLUGIN_FILE"       -o "$PLUGIN_DIR/$PLUGIN_FILE"
curl -fsSL "$GITHUB_RAW/rpi_gpio/requirements.txt"   -o "$PLUGIN_DIR/requirements.txt"

echo "  Plugin files saved to: $PLUGIN_DIR"

# ── Step 4: Create a dedicated Python virtual environment ─────────────────────
echo "[3/6] Creating Python virtual environment..."

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$PLUGIN_DIR/requirements.txt" -q

echo "  Virtual environment ready at: $VENV_DIR"

# ── Step 5: Enable and start the pigpiod GPIO daemon ─────────────────────────
echo "[4/6] Enabling pigpiod daemon..."

# pigpiod must be running before the plugin can control GPIO pins
systemctl enable pigpiod  2>/dev/null || true
systemctl start  pigpiod  2>/dev/null || true

echo "  pigpiod enabled and started."

# ── Step 6: Patch OpenPLC bug in plcapp_management.py ───────────────────────
echo "[5/7] Applying OpenPLC V4 bugfix (plcapp_management.py)..."

# Bug: update_plugin_configurations() disables ALL enabled plugins when an
# uploaded program ZIP has no conf/ directory (which is the case for most
# programs). This silently sets rpi_gpio to enabled=0 on every program upload.
#
# Fix: only disable plugins that actually require a config file (config_path
# is not empty). Hardware drivers like rpi_gpio have an empty config_path
# and must be left untouched by program uploads.
#
# Affected line in webserver/plcapp_management.py (around line 188):
#   Before:  if plugin.enabled:
#   After:   if plugin.enabled and plugin.config_path:

PLCAPP="$OPENPLC_DIR/webserver/plcapp_management.py"

if grep -q "if plugin.enabled and plugin.config_path:" "$PLCAPP" 2>/dev/null; then
    echo "  Bugfix already applied, skipping."
else
    # Use Python to apply the fix safely without relying on line numbers
    python3 - <<PYEOF
import re, sys

path = "$PLCAPP"
with open(path, "r") as f:
    src = f.read()

# Replace only the first occurrence of the bare condition inside the
# update_plugin_configurations function
old = "if plugin.enabled:"
new = "if plugin.enabled and plugin.config_path:  # patched: skip hardware drivers with no config"

if old not in src:
    print("  WARNING: Expected pattern not found — file may have changed upstream.")
    sys.exit(0)

src = src.replace(old, new, 1)
with open(path, "w") as f:
    f.write(src)

print("  Bugfix applied successfully.")
PYEOF
fi

# ── Step 7: Register the plugin in plugins.conf ──────────────────────────────
echo "[6/7] Registering plugin in plugins.conf..."

# plugins.conf format: name,path,enabled,type,config_path,venv_path
PLUGIN_ENTRY="$PLUGIN_NAME,./core/src/drivers/plugins/python/$PLUGIN_NAME/$PLUGIN_FILE,1,0,,./venvs/$PLUGIN_NAME"

if grep -q "^$PLUGIN_NAME," "$PLUGINS_CONF" 2>/dev/null; then
    # Entry exists — update it (ensures enabled=1 even if it was 0)
    sed -i "s|^$PLUGIN_NAME,.*|$PLUGIN_ENTRY|" "$PLUGINS_CONF"
    echo "  Existing entry updated in plugins.conf."
else
    # No entry yet — append it
    echo "$PLUGIN_ENTRY" >> "$PLUGINS_CONF"
    echo "  New entry added to plugins.conf."
fi



# ── Step 8: Restart OpenPLC and verify ───────────────────────────────────────
echo "[7/7] Restarting OpenPLC Runtime service..."

systemctl restart openplc-runtime.service
sleep 4  # Give the service a moment to fully initialise

if systemctl is-active --quiet openplc-runtime.service; then
    echo ""
    echo "============================================"
    echo " Installation complete!"
    echo "============================================"
    echo ""
    echo " GPIO mapping active:"
    echo "   Outputs  GPIO21=%QX0.0  GPIO20=%QX0.1  GPIO16=%QX0.2  GPIO12=%QX0.3"
    echo "   Inputs   GPIO26=%IX0.0  GPIO19=%IX0.1  GPIO13=%IX0.2  GPIO5=%IX0.3"
    echo "            GPIO22=%IX0.4  GPIO27=%IX0.5  GPIO17=%IX0.6  GPIO4=%IX0.7"
    echo ""
    echo " Compile and upload a PLC program from the OpenPLC Editor"
    echo " and the GPIO pins will respond immediately."
    echo ""
else
    echo ""
    echo "ERROR: OpenPLC Runtime failed to start after installation."
    echo "  Check the logs with:"
    echo "    sudo journalctl -u openplc-runtime.service -n 60"
    exit 1
fi
