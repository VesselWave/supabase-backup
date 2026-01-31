#!/bin/bash
set -e

# Prioritize local node_modules/.bin
export PATH="$PWD/node_modules/.bin:$PATH"

# Logging configuration
LOG_DIR="$HOME/.config/supabase-backup/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/restore.log"

# Redirect stdout and stderr to the log file while keeping stdout on the terminal
exec > >(tee -a "$LOG_FILE") 2>&1


# Default env file
ENV_FILE=".env"

# Locking mechanism to prevent concurrent backup/restore
exec 200>/tmp/supabase_backup_restore.lock
if ! flock -n 200; then
    echo "Error: Another backup or restore process is running."
    exit 1
fi

# Python Virtual Environment path (fixed, not from env)
VENV_PATH="./venv"

# Ensure Python Virtual Environment is ready (needed for interactive menu)
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_PATH"
fi
"$VENV_PATH/bin/pip" install -r requirements.txt | sed -u '/Requirement already satisfied/d'

PYTHON_EXEC="$VENV_PATH/bin/python3"

# Parsing Arguments
IS_TEST_MODE=false
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --test)
        IS_TEST_MODE=true
        shift
        ;;
        -y|--yes)
        SKIP_CONFIRM=true
        shift
        ;;
        --env-file|-e)
        ENV_FILE="$2"
        shift 2
        ;;
        *)
        echo "Unknown argument: $1"
        echo "Usage: $0 [--test] [-y|--yes] [--env-file|-e <path>]"
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

# Configuration (must be after env file is loaded)
LOCAL_BACKUP_DIR=$(eval echo "${LOCAL_BACKUP_DIR:-./backups}")

# Ensure backup directory exists
if [ ! -d "$LOCAL_BACKUP_DIR" ]; then
    echo "Error: Local backup directory '$LOCAL_BACKUP_DIR' not found."
    exit 1
fi

# INTERACTIVE SELECTION
RESTORE_DB=true
RESTORE_STORAGE=true
ARCHIVE_NAME="Local"

if [ "$SKIP_CONFIRM" = false ]; then
    echo "Starting interactive selection..."
    # Run interactive.py and capture JSON
    # we use a temporary file to capture output reliably or just parse stdout
    # interactive.py prints only JSON to stdout
    SELECTION_JSON=$($PYTHON_EXEC interactive.py)
    
    # Parse JSON (using python one-liner to avoid jq dependency)
    # Parse JSON efficiently in one go
    read -r ARCHIVE_NAME RESTORE_DB_VAL RESTORE_STORAGE_VAL <<< $(echo "$SELECTION_JSON" | $PYTHON_EXEC -c "import sys, json; d=json.load(sys.stdin); print(d['archive'], d['restore_db'], d['restore_storage'])")
    
    if [ "$RESTORE_DB_VAL" == "True" ]; then RESTORE_DB=true; else RESTORE_DB=false; fi
    if [ "$RESTORE_STORAGE_VAL" == "True" ]; then RESTORE_STORAGE=true; else RESTORE_STORAGE=false; fi
    
    echo "Selected Archive: $ARCHIVE_NAME"
    echo "Restore Database: $RESTORE_DB"
    echo "Restore Storage: $RESTORE_STORAGE"
fi

if [ "$IS_TEST_MODE" = true ]; then
    export TARGET_PROJECT_REF="$TEST_SUPABASE_PROJECT_REF"
    export TARGET_DB_PASSWORD="$TEST_SUPABASE_DB_PASSWORD"
    export TARGET_SERVICE_ROLE_KEY="$TEST_SUPABASE_SERVICE_ROLE_KEY"
    
    echo "--- RESTORE MODE: TEST INSTANCE ---"
    echo "Target Project: $TARGET_PROJECT_REF"
    
    if [ "$SKIP_CONFIRM" = true ]; then
        echo "Confirmation skipped (--yes)."
    else
        read -p "Restoring to TEST instance. This will WIPE ALL DATA and mirror the backup. Continue? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Aborted."
            exit 1
        fi
    fi
else
    echo "--- RESTORE MODE: MANUAL TARGET ---"
    
    read -p "Enter Target Project Reference: " INPUT_PROJECT_REF
    if [ -z "$INPUT_PROJECT_REF" ]; then echo "Project Reference is required."; exit 1; fi
    export TARGET_PROJECT_REF="$INPUT_PROJECT_REF"

    # Password prompt (hidden input)
    read -s -p "Enter Target Database Password: " INPUT_DB_PASSWORD
    echo
    if [ -z "$INPUT_DB_PASSWORD" ]; then echo "Database Password is required."; exit 1; fi
    export TARGET_DB_PASSWORD="$INPUT_DB_PASSWORD"
    
    # Service Key prompt (hidden input)
    read -s -p "Enter Target Service Role Key: " INPUT_SERVICE_KEY
    echo
    if [ -z "$INPUT_SERVICE_KEY" ]; then echo "Service Role Key is required."; exit 1; fi
    export TARGET_SERVICE_ROLE_KEY="$INPUT_SERVICE_KEY"
    
    # Access Token prompt (hidden input)
    read -s -p "Enter Supabase Access Token: " INPUT_ACCESS_TOKEN
    echo
    if [ -z "$INPUT_ACCESS_TOKEN" ]; then echo "Supabase Access Token is required."; exit 1; fi
    export SUPABASE_ACCESS_TOKEN="$INPUT_ACCESS_TOKEN"

    echo ""
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "WARNING: You are about to restore to a MANUAL target: $TARGET_PROJECT_REF"
    echo "This is NOT the configured test instance."
    echo "This will WIPE ALL DATA on $TARGET_PROJECT_REF and make it exactly like the backup."
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
    
    read -p "Are you sure you want to WIPE INITIAL DATA and restore? (Type 'yes' to confirm): " CONFIRM_1
    if [ "$CONFIRM_1" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
    
    read -p "Please type the target project reference ($TARGET_PROJECT_REF) to confirm: " CONFIRM_2
    if [ "$CONFIRM_2" != "$TARGET_PROJECT_REF" ]; then
        echo "Confirmation failed. Aborted."
        exit 1
    fi
fi

# HANDLE EXTRACTION

# Log Configuration Context
echo "Restore Configuration:"
echo "  Archive Name: $ARCHIVE_NAME"
echo "  Target Project: $TARGET_PROJECT_REF"
echo "  Test Mode: $IS_TEST_MODE"
echo "  Restore DB: $RESTORE_DB"
echo "  Restore Storage: $RESTORE_STORAGE"
echo "------------------------------------------------"

if [ "$ARCHIVE_NAME" = "Extract" ]; then
    echo "Using previously extracted archive from BORG_EXTRACT_DIR..."
    
    # BORG_EXTRACT_DIR is required
    if [ -z "$BORG_EXTRACT_DIR" ]; then
        echo "Error: BORG_EXTRACT_DIR must be set in .env to use the Extract option."
        exit 1
    fi
    
    BORG_EXTRACT_DIR=$(eval echo "$BORG_EXTRACT_DIR")
    
    if [ ! -d "$BORG_EXTRACT_DIR" ]; then
        echo "Error: BORG_EXTRACT_DIR '$BORG_EXTRACT_DIR' does not exist."
        exit 1
    fi
    
    echo "Using extraction directory: $BORG_EXTRACT_DIR"
    export LOCAL_BACKUP_DIR="$BORG_EXTRACT_DIR"
    
elif [ "$ARCHIVE_NAME" != "Local" ]; then
    echo "Extracting archive '$ARCHIVE_NAME' from Borg..."
    
    # Ensure Borg is ok with valid repo
    export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
    export BORG_RELOCATED_REPO_ACCESS_IS_OK=yes
    BORG_REPO=$(eval echo "${BORG_REPO:-./borg-repo}")

    # BORG_EXTRACT_DIR is required when extracting archives
    if [ -z "$BORG_EXTRACT_DIR" ]; then
        echo "Error: BORG_EXTRACT_DIR must be set in .env when extracting Borg archives."
        echo "Please add BORG_EXTRACT_DIR to your .env file (e.g., BORG_EXTRACT_DIR=\$HOME/backups/supabase/extract)"
        exit 1
    fi
    
    BORG_EXTRACT_DIR=$(eval echo "$BORG_EXTRACT_DIR")
    
    if [ -d "$BORG_EXTRACT_DIR" ]; then
        echo "Cleaning extraction directory: $BORG_EXTRACT_DIR (to ensure exact match with archive)"
        # Delete everything inside to start fresh
        find "$BORG_EXTRACT_DIR" -mindepth 1 -delete
    else
        echo "Creating extraction directory: $BORG_EXTRACT_DIR"
        mkdir -p "$BORG_EXTRACT_DIR"
    fi

    echo "Extraction Target: $BORG_EXTRACT_DIR"
    
    echo "Calculating strip path from archive contents..."
    
    # Determine common path depth to strip using Python helper
    # Logic: Find common path. If ends in 'database' or 'storage', step back one level.
    STRIP_COUNT=$(borg list --json-lines "$BORG_REPO::$ARCHIVE_NAME" | $PYTHON_EXEC interactive.py --calculate-strip)
    
    echo "Detected strip count: $STRIP_COUNT"
    
    cd "$BORG_EXTRACT_DIR"
    borg extract --list --strip-components "$STRIP_COUNT" "$BORG_REPO::$ARCHIVE_NAME"
    cd - > /dev/null
    echo "Extraction complete."
    
    # Ensure downstream scripts use the extracted location
    export LOCAL_BACKUP_DIR="$BORG_EXTRACT_DIR"
fi

echo "--- Starting Supabase Restore: $(date) ---"

# 1. Update Supabase CLI (Skipped - User responsibility to install dependencies)
# npm install supabase@latest --silent


# (Venv setup moved to top)

# 2. Restore Phase (includes atomic wipe)
if [ "$RESTORE_DB" = true ]; then
    echo "--- Phase 1: Restoring Database (with atomic wipe) ---"
    $PYTHON_EXEC database.py restore --env-file "$ENV_FILE"
else
    echo "Skipping database restore."
fi

# 3. Storage Phase
if [ "$RESTORE_STORAGE" = true ]; then
    echo "--- Phase 3: Restoring Storage Content ---"
    # Note: storage.py handles its own 'wipe' logic by deleting extra files
    $PYTHON_EXEC storage.py restore --env-file "$ENV_FILE"
else
    echo "Skipping storage restore."
fi

echo "--- Restore Completed Successfully: $(date) ---"
