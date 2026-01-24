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

# Configuration
LOCAL_BACKUP_DIR=$(eval echo "${LOCAL_BACKUP_DIR:-./backups}")
VENV_PATH="./venv"

# Ensure backup directory exists
if [ ! -d "$LOCAL_BACKUP_DIR" ]; then
    echo "Error: Local backup directory '$LOCAL_BACKUP_DIR' not found."
    exit 1
fi

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

echo "--- Starting Supabase Restore: $(date) ---"

# 1. Update Supabase CLI
echo "Updating Supabase CLI..."
npm install supabase@latest --silent

# 2. Handle Python Virtual Environment
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_PATH"
    "$VENV_PATH/bin/pip" install -r requirements.txt
fi

# Use the python from the venv
PYTHON_EXEC="$VENV_PATH/bin/python3"

# 3. Database Restore
echo "Restoring database..."
$PYTHON_EXEC database.py restore

# 4. Storage Restore
echo "Restoring storage..."
$PYTHON_EXEC storage.py restore

echo "--- Restore Completed Successfully: $(date) ---"
