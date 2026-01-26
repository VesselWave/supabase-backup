#!/bin/bash
set -e

# Supabase Backup Installation Script
# This script installs systemd services and configures paths based on the current location.

INSTALL_DIR=$(pwd)
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
LOGROTATE_SOURCE="systemd/supabase-backup"
LOGROTATE_DEST="supabase-backup.logrotate"

if [ -f "$LOGROTATE_SOURCE" ]; then
    echo "Generating custom logrotate configuration..."
    cp "$LOGROTATE_SOURCE" "$LOGROTATE_DEST"
    
    # Replace /home/user with actual HOME
    sed -i "s|/home/user|$HOME|g" "$LOGROTATE_DEST"
    
    # Replace 'create 0640 user user' with actual USER and GROUP
    CURRENT_USER=$(id -u -n)
    CURRENT_GROUP=$(id -g -n)
    sed -i "s|create 0640 user user|create 0640 $CURRENT_USER $CURRENT_GROUP|g" "$LOGROTATE_DEST"
    
    echo "Generated $LOGROTATE_DEST with correct paths and user."
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
