#!/bin/bash
set -e

# Ensure local node_modules/.bin is in PATH for the supabase CLI
export PATH="$PATH:$PWD/node_modules/.bin"

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "Error: .env file not found."
    exit 1
fi

# Locking mechanism to prevent concurrent backup/restore
exec 200>/tmp/supabase_backup_restore.lock
if ! flock -n 200; then
    echo "Error: Another backup or restore process is running."
    exit 1
fi


# Configuration
LOCAL_BACKUP_DIR=$(eval echo "${LOCAL_BACKUP_DIR:-./backups}")
VENV_PATH="./venv"

# Ensure backup directory exists
if [ ! -d "$LOCAL_BACKUP_DIR" ]; then
    echo "Error: Local backup directory '$LOCAL_BACKUP_DIR' not found."
    exit 1
fi

# Ensure Python Virtual Environment is ready (needed for interactive menu)
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_PATH"
fi
"$VENV_PATH/bin/pip" install -r requirements.txt > /dev/null

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
        *)
        echo "Unknown argument: $1"
        shift
        ;;
    esac
done

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
    ARCHIVE_NAME=$(echo "$SELECTION_JSON" | $PYTHON_EXEC -c "import sys, json; print(json.load(sys.stdin)['archive'])")
    RESTORE_DB_VAL=$(echo "$SELECTION_JSON" | $PYTHON_EXEC -c "import sys, json; print(json.load(sys.stdin)['restore_db'])")
    RESTORE_STORAGE_VAL=$(echo "$SELECTION_JSON" | $PYTHON_EXEC -c "import sys, json; print(json.load(sys.stdin)['restore_storage'])")
    
    if [ "$RESTORE_DB_VAL" == "True" ]; then RESTORE_DB=true; else RESTORE_DB=false; fi
    if [ "$RESTORE_STORAGE_VAL" == "True" ]; then RESTORE_STORAGE=true; else RESTORE_STORAGE=false; fi
    
    echo "Selected Archive: $ARCHIVE_NAME"
    echo "Restore Database: $RESTORE_DB"
    echo "Restore Storage: $RESTORE_STORAGE"
fi

# HANDLE EXTRACTION
if [ "$ARCHIVE_NAME" != "Local" ]; then
    echo "Extracting archive '$ARCHIVE_NAME' from Borg..."
    
    # Ensure Borg is ok with valid repo
    export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
    export BORG_RELOCATED_REPO_ACCESS_IS_OK=yes
    BORG_REPO=$(eval echo "${BORG_REPO:-./borg-repo}")

    # Determine extraction target (defaults to LOCAL_BACKUP_DIR)
    BORG_EXTRACT_DIR=$(eval echo "${BORG_EXTRACT_DIR:-$LOCAL_BACKUP_DIR}")
    
    if [ ! -d "$BORG_EXTRACT_DIR" ]; then
        echo "Creating extraction directory: $BORG_EXTRACT_DIR"
        mkdir -p "$BORG_EXTRACT_DIR"
    fi

    echo "Extraction Target: $BORG_EXTRACT_DIR"
    
    cd "$BORG_EXTRACT_DIR"
    borg extract --list "$BORG_REPO::$ARCHIVE_NAME"
    cd - > /dev/null
    echo "Extraction complete."
    
    # Ensure downstream scripts use the extracted location
    export LOCAL_BACKUP_DIR="$BORG_EXTRACT_DIR"
fi

echo "--- Starting Supabase Restore: $(date) ---"

# 1. Update Supabase CLI
echo "Updating Supabase CLI..."
npm install supabase@latest --silent

# (Venv setup moved to top)

# 3. Database Restore
if [ "$RESTORE_DB" = true ]; then
    echo "Restoring database..."
    $PYTHON_EXEC database.py restore
else
    echo "Skipping database restore."
fi

# 4. Storage Restore
if [ "$RESTORE_STORAGE" = true ]; then
    echo "Restoring storage..."
    $PYTHON_EXEC storage.py restore
else
    echo "Skipping storage restore."
fi

echo "--- Restore Completed Successfully: $(date) ---"
