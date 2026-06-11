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
FINAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-./backups}"
if [[ "$FINAL_BACKUP_DIR" == "~"* ]]; then
    FINAL_BACKUP_DIR="${FINAL_BACKUP_DIR/#\~/$HOME}"
fi
DUMP_DIR="$FINAL_BACKUP_DIR"
BORG_REPO="${BORG_REPO:-./borg-repo}"
if [[ "$BORG_REPO" == "~"* ]]; then
    BORG_REPO="${BORG_REPO/#\~/$HOME}"
fi
RETENTION_DAYS="${BORG_RETENTION_DAYS:-21}"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
VENV_PATH="./venv"
export COMMAND_TIMEOUT_SEC="${COMMAND_TIMEOUT_SEC:-7200}"

BACKUP_PARENT_DIR="$(dirname "$FINAL_BACKUP_DIR")"
mkdir -p "$BACKUP_PARENT_DIR"
STAGING_BACKUP_DIR="$(mktemp -d "$BACKUP_PARENT_DIR/.supabase-backup-${TIMESTAMP}.XXXXXX")"

cleanup() {
    local exit_code=$?
    trap - EXIT
    if [ -n "$STAGING_BACKUP_DIR" ] && [ -d "$STAGING_BACKUP_DIR" ]; then
        rm -rf "$STAGING_BACKUP_DIR"
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# Child scripts write into staging. Promote to latest only after full success.
export LOCAL_BACKUP_DIR="$STAGING_BACKUP_DIR"
mkdir -p "$LOCAL_BACKUP_DIR"

# Check dependencies
# Check for local supabase CLI and install if missing
if [ ! -x "./node_modules/.bin/supabase" ]; then
    echo "Supabase CLI not found in ./node_modules/.bin/supabase. Installing dependencies..."
    npm install
fi

command -v borg >/dev/null 2>&1 || { echo >&2 "Error: 'borg' is required but not installed. Aborting."; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo >&2 "Error: 'rsync' is required but not installed. Aborting."; exit 1; }

echo "--- Starting Supabase Backup: $TIMESTAMP ---"
echo "Command timeout: ${COMMAND_TIMEOUT_SEC}s"
echo "Staging backup dir: $STAGING_BACKUP_DIR"
echo "Final backup dir: $FINAL_BACKUP_DIR"

ensure_podman_file_log_driver() {
    # Supabase CLI streams pg_dump output through Docker/Podman logs. Podman's
    # journald driver truncates large log records, which can cut schema.sql and
    # leave the CLI stuck following logs forever. Force file-backed logs for
    # containers created through the Podman Docker API.
    local current_driver
    current_driver="$(podman info --format '{{.Host.LogDriver}}' 2>/dev/null || true)"
    if [ "$current_driver" = "k8s-file" ]; then
        return 0
    fi

    local containers_conf_dir="$HOME/.config/containers"
    local containers_conf="$containers_conf_dir/containers.conf"
    mkdir -p "$containers_conf_dir"

    if [ -f "$containers_conf" ]; then
        cp "$containers_conf" "$containers_conf.bak.$(date +%Y%m%d%H%M%S)"
    fi

    python3 - "$containers_conf" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text() if path.exists() else ""
lines = text.splitlines()
out = []
in_containers = False
seen_containers = False
set_driver = False

for line in lines:
    section = re.match(r"\s*\[([^]]+)\]\s*$", line)
    if section:
        if in_containers and not set_driver:
            out.append('log_driver = "k8s-file"')
            set_driver = True
        in_containers = section.group(1).strip() == "containers"
        seen_containers = seen_containers or in_containers
        out.append(line)
        continue

    if in_containers and re.match(r"\s*log_driver\s*=", line):
        if not set_driver:
            out.append('log_driver = "k8s-file"')
            set_driver = True
        else:
            out.append("# " + line)
        continue

    out.append(line)

if in_containers and not set_driver:
    out.append('log_driver = "k8s-file"')
elif not seen_containers:
    if out and out[-1].strip():
        out.append("")
    out.extend(["[containers]", 'log_driver = "k8s-file"'])

path.write_text("\n".join(out) + "\n")
PY

    # If the user Podman API service is already running, restart it so it reads
    # the updated containers.conf before Supabase CLI creates dump containers.
    systemctl --user restart podman.socket 2>/dev/null || true
    systemctl --user try-restart podman.service 2>/dev/null || true

    current_driver="$(podman info --format '{{.Host.LogDriver}}' 2>/dev/null || true)"
    if [ "$current_driver" != "k8s-file" ]; then
        echo "Error: Podman log driver is '$current_driver', expected 'k8s-file'." >&2
        echo "Supabase database dumps may hang with Podman's journald log driver." >&2
        exit 1
    fi
}

# Enforce Container Runtime (Podman or Docker)
if command -v podman >/dev/null 2>&1; then
    # Default to Podman if available
    # Only set DOCKER_HOST if not already set by the user
    if [ -z "$DOCKER_HOST" ]; then
        export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"
    fi
    ensure_podman_file_log_driver
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
mkdir -p "$LOCAL_BACKUP_DIR"

# 3. Database Backup (using Supabase CLI via Python script)
echo "Dumping database..."
$PYTHON_EXEC database.py backup --env-file "$ENV_FILE"

# 3.5 Edge Functions Backup
echo "Backing up Edge Functions..."
$PYTHON_EXEC edge_functions.py backup --env-file "$ENV_FILE"

# 4. Storage Sync (Python script / rclone)
echo "Syncing storage blocks..."
$PYTHON_EXEC storage.py backup --env-file "$ENV_FILE"

# Promote staged backup only after all backup phases succeed
echo "Promoting staged backup to $FINAL_BACKUP_DIR..."
mkdir -p "$FINAL_BACKUP_DIR"
rsync -a --delete "$STAGING_BACKUP_DIR"/ "$FINAL_BACKUP_DIR"/
export LOCAL_BACKUP_DIR="$FINAL_BACKUP_DIR"

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
