#!/bin/bash
set -e

# Ensure local node_modules/.bin is in PATH for the supabase CLI
export PATH="$PATH:$PWD/node_modules/.bin"

# Load environment variables
if [ -f .env ]; then
    # Use 'set -a' to export variables from the sourced file
    set -a
    source .env
    set +a
else
    echo "Error: .env file not found."
    exit 1
fi

# Borg configuration for non-interactive use
export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
export BORG_RELOCATED_REPO_ACCESS_IS_OK=yes

# Configuration
# Expand variables like $HOME or ~ if they exist in the strings
LOCAL_BACKUP_DIR=$(eval echo "${LOCAL_BACKUP_DIR:-./backups}")
DUMP_DIR="$LOCAL_BACKUP_DIR"
BORG_REPO=$(eval echo "${BORG_REPO:-./borg-repo}")
RETENTION_DAYS="${BORG_RETENTION_DAYS:-21}"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
VENV_PATH="./venv"

# Ensure backup directory exists
mkdir -p "$LOCAL_BACKUP_DIR"

# Check dependencies
command -v borg >/dev/null 2>&1 || { echo >&2 "Error: 'borg' is required but not installed. Aborting."; exit 1; }

echo "--- Starting Supabase Backup: $TIMESTAMP ---"

# 1. Handle Python Virtual Environment
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_PATH"
    "$VENV_PATH/bin/pip" install -r requirements.txt
fi

# Use the python from the venv
PYTHON_EXEC="$VENV_PATH/bin/python3"

# 2. Prepare dump directory
mkdir -p "$DUMP_DIR"

# 3. Database Backup (using Supabase CLI via Python script)
echo "Dumping database..."
$PYTHON_EXEC database.py backup

# 4. Storage Sync (Python script / rclone)
echo "Syncing storage blocks..."
$PYTHON_EXEC storage.py backup

# 5. Borg Backup
echo "Starting Borg backup..."

# Initialize Borg repo if it doesn't exist
if [ ! -d "$BORG_REPO" ]; then
    echo "Initializing new Borg repository (no encryption)..."
    borg init --encryption=none "$BORG_REPO"
fi

# Create archive
echo "Creating archive: $TIMESTAMP"
borg create --stats --progress \
    "$BORG_REPO::$TIMESTAMP" \
    "$DUMP_DIR"

# 6. Retention Management
echo "Pruning old backups (Retention: $RETENTION_DAYS days)..."
borg prune --list --keep-within="${RETENTION_DAYS}d" "$BORG_REPO"
borg compact "$BORG_REPO"

echo "--- Backup Completed Successfully: $(date) ---"
