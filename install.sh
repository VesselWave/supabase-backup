#!/bin/bash
set -e

# Supabase Backup Installation Script
# This script installs systemd services and configures paths based on the current location.

# Check for required tools
if ! command -v envsubst >/dev/null 2>&1; then
    echo "Error: 'envsubst' is required but not installed."
    echo "Please install it (usually part of 'gettext' or 'gettext-base' package)."
    exit 1
fi

INSTALL_DIR=$(pwd)
COPY_TARGET=""

# Argument parsing
while [[ $# -gt 0 ]]; do
    case $1 in
        --copy-to)
            COPY_TARGET="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: ./install.sh [--copy-to <path>]"
            exit 1
            ;;
    esac
done

if [ -n "$COPY_TARGET" ]; then
    # Resolve absolute path for target
    TARGET_DIR=$(eval echo "$COPY_TARGET")
    
    echo "Installing to peristent location: $TARGET_DIR"
    mkdir -p "$TARGET_DIR"
    
    # Copy files (excluding venv, .git, and node_modules to ensure clean state)
    echo "Copying files..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -av --exclude 'venv' --exclude '.git' --exclude 'node_modules' --exclude 'backups' . "$TARGET_DIR/"
    else
        # Fallback to cp
        cp -R . "$TARGET_DIR/"
        rm -rf "$TARGET_DIR/venv" "$TARGET_DIR/.git" "$TARGET_DIR/backups" "$TARGET_DIR/node_modules"
    fi
    
    # Update INSTALL_DIR to point to the new location
    INSTALL_DIR="$TARGET_DIR"
    
    # Install dependencies in the new location if needed
    if [ ! -d "$INSTALL_DIR/node_modules" ]; then
        echo "Installing npm dependencies in $INSTALL_DIR..."
        (cd "$INSTALL_DIR" && npm install)
    fi
else
    echo "Installing in-place... (Directory: $INSTALL_DIR)"
    
    # Warn if installing from a temporary location
    if [[ "$INSTALL_DIR" == *"/tmp"* ]] || [[ "$INSTALL_DIR" == *"/Downloads"* ]]; then
        echo "WARNING: You are installing from a temporary directory."
        echo "If you delete this folder, the backup service will fail."
        echo "Consider moving this repo to a permanent location or usage './install.sh --copy-to ~/.local/share/supabase-backup'"
        echo "Press Ctrl+C to abort or Enter to continue."
        read -r
    fi
fi

USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
LOG_DIR="$HOME/.config/supabase-backup/logs"

echo "Installing Supabase Backup from: $INSTALL_DIR"
echo "Target Systemd Directory: $USER_SYSTEMD_DIR"

# 1. Prepare Directories
mkdir -p "$USER_SYSTEMD_DIR"
mkdir -p "$LOG_DIR"

# 2. Generate and Copy Service Files
echo "Generating systemd service files from templates..."

# Export INSTALL_DIR so envsubst can pick it up
export INSTALL_DIR

# Process templates
# We use a temp dir to avoid creating artifact files in the repo
TEMP_GEN_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_GEN_DIR"' EXIT

envsubst < systemd/supabase-backup.service.template > "$TEMP_GEN_DIR/supabase-backup.service"
envsubst < systemd/supabase-restore.service.template > "$TEMP_GEN_DIR/supabase-restore.service"

echo "Copying service and timer files..."
# Copy the generated services
cp "$TEMP_GEN_DIR/supabase-backup.service" "$TEMP_GEN_DIR/supabase-restore.service" "$USER_SYSTEMD_DIR/"
# Copy the static timers
cp systemd/supabase-backup.timer systemd/supabase-restore.timer "$USER_SYSTEMD_DIR/"

# 3. Reload Systemd
echo "Reloading systemd user daemon..."
systemctl --user daemon-reload

# 5. Generate Logrotate Config (Optional)
# The default logrotate config assumes /home/user and user:user. We fix this here.
LOGROTATE_SOURCE="systemd/supabase-backup.logrotate.template"
LOGROTATE_DEST="supabase-backup.logrotate"

if [ -f "$LOGROTATE_SOURCE" ]; then
    echo "Generating custom logrotate configuration..."
    
    # Export variables for envsubst
    export USER=$(id -u -n)
    export GROUP=$(id -g -n)
    export HOME
    
    envsubst < "$LOGROTATE_SOURCE" > "$LOGROTATE_DEST"
    
    echo "Generated $LOGROTATE_DEST with correct paths and user ($USER:$GROUP)."
fi

echo ""
echo "--- Installation Complete ---"
echo "1. Enable Backup Timer (Daily):"
echo "   systemctl --user enable --now supabase-backup.timer"
echo ""
echo "2. Enable Restore Timer (Weekly):"
echo "   systemctl --user enable --now supabase-restore.timer"
echo ""
echo "3. (Optional) Log Rotation:"
echo "   If you have sudo access, copy the generated logrotate file:"
echo "   sudo cp $LOGROTATE_DEST /etc/logrotate.d/supabase-backup"
echo "   sudo chown root:root /etc/logrotate.d/supabase-backup"
