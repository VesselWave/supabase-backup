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

# Borg configuration (if needed in future, but restore uses local files for now)
# Configuration
LOCAL_BACKUP_DIR=$(eval echo "${LOCAL_BACKUP_DIR:-./backups}")
VENV_PATH="./venv"

# Ensure backup directory exists (source)
if [ ! -d "$LOCAL_BACKUP_DIR" ]; then
    echo "Error: Local backup directory '$LOCAL_BACKUP_DIR' not found."
    exit 1
fi

# Check dependencies
# (No additional CLI dependencies for restore)

echo "--- Starting Supabase Restore to TEST: $(date) ---"

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
echo "Restoring database to TEST environment..."
$PYTHON_EXEC database.py restore

# 4. Storage Restore
echo "Restoring storage to TEST environment..."
$PYTHON_EXEC storage.py restore

echo "--- Restore Completed Successfully: $(date) ---"
