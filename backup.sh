#!/bin/bash
set -e

# Logging configuration
LOG_DIR="$HOME/.config/supabase-backup/logs"
if ! mkdir -p "$LOG_DIR"; then
    echo "Error: Failed to create log directory $LOG_DIR" >&2
    exit 1
fi
LOG_FILE="$LOG_DIR/backup.log"

# Redirect stdout and stderr to the log file while keeping stdout on the terminal
exec > >(tee -a "$LOG_FILE") 2>&1


# Ensure local node_modules/.bin is in PATH for the supabase CLI
export PATH="$PWD/node_modules/.bin:$PATH"

# Parse arguments
ENV_FILE=".env"

while [[ $# -gt 0 ]]; do
    case $1 in
        --env-file|-e)
        ENV_FILE="$2"
        shift 2
        ;;
        *)
        echo "Unknown argument: $1"
        echo "Usage: $0 [--env-file|-e <path>]"
        exit 1
        ;;
    esac
done

# Load environment variables
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "Error: Environment file '$ENV_FILE' not found."
    exit 1
fi

# Locking mechanism to prevent concurrent backup/restore
exec 200>/tmp/supabase_backup_restore.lock
if ! flock -n 200; then
    echo "Error: Another backup or restore process is running."
    exit 1
fi

# Borg configuration for non-interactive use
export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
export BORG_RELOCATED_REPO_ACCESS_IS_OK=yes

# Configuration
# Expand variables like $HOME or ~ if they exist in the strings
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-./backups}"
if [[ "$LOCAL_BACKUP_DIR" == "~"* ]]; then
    LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR/#\~/$HOME}"
fi
DUMP_DIR="$LOCAL_BACKUP_DIR"
BORG_REPO="${BORG_REPO:-./borg-repo}"
if [[ "$BORG_REPO" == "~"* ]]; then
    BORG_REPO="${BORG_REPO/#\~/$HOME}"
fi
RETENTION_DAYS="${BORG_RETENTION_DAYS:-21}"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
VENV_PATH="./venv"

# Ensure backup directory exists
mkdir -p "$LOCAL_BACKUP_DIR"

# Check dependencies
# Check for local supabase CLI and install if missing
if [ ! -x "./node_modules/.bin/supabase" ]; then
    echo "Supabase CLI not found in ./node_modules/.bin/supabase. Installing dependencies..."
    npm install
fi

command -v borg >/dev/null 2>&1 || { echo >&2 "Error: 'borg' is required but not installed. Aborting."; exit 1; }

echo "--- Starting Supabase Backup: $TIMESTAMP ---"

# Enforce Container Runtime (Podman or Docker)
if command -v podman >/dev/null 2>&1; then
    # Default to Podman if available
    # Only set DOCKER_HOST if not already set by the user
    if [ -z "$DOCKER_HOST" ]; then
        export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"
    fi
     echo "Using Podman at $DOCKER_HOST"
elif command -v docker >/dev/null 2>&1; then
    # Fallback to Docker
    echo "Using Docker (Podman not found)"
    echo "WARNING: Docker requires root privileges or the 'docker' group, which is less secure than rootless Podman."
    # Docker usually defaults to a known socket, or respects DOCKER_HOST if set.
    # We do not override DOCKER_HOST for Docker unless necessary, but standard docker doesn't need it explicitly set if strictly following standards.
else
    echo "Error: Neither 'podman' nor 'docker' is installed. One is required."
    exit 1
fi

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
$PYTHON_EXEC database.py backup --env-file "$ENV_FILE"

# 3.5 Edge Functions Backup
echo "Backing up Edge Functions..."
$PYTHON_EXEC edge_functions.py backup --env-file "$ENV_FILE"

# 4. Storage Sync (Python script / rclone)
echo "Syncing storage blocks..."
$PYTHON_EXEC storage.py backup --env-file "$ENV_FILE"

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
