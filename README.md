# Supabase Backup System

This project provides a robust backup and restore solution for Supabase, designed to handle database schemas, data, migration history, and storage buckets. It uses **Borg Backup** for deduplicated, versioned storage and supports complex restore scenarios including permission handling and schema drift.

## Features

### Database (`database.py`)
- **Complete Environment Capture**:
  - Roles, Schema, and Data.
  - **Migration History**: Dumps `supabase_migrations` schema to preserve migration state.
  - **Schema Drift**: Captures internal schema changes (`auth`, `storage`) via `supabase db diff`.
- **Robust Restore**:
  - **Auto-Cleaning**: Automatically sanitizes dumps to remove restricted permissions (e.g., `GRANT ... TO "postgres"`) that fail in managed environments.
  - **Schema Patching**: Comments out `ALTER ... OWNER TO "supabase_admin"` to prevent ownership errors.
  - **Data Skipping**: Smart-skips internal system tables that cause permission errors (e.g., `storage.buckets_vectors`).
  - **Auto-Wipe**: Recreates the `public` schema and truncates `auth`/`storage` tables before restoration to ensure a clean state.
  - **Requirements Scanning**: Analyzes backup files for usage of Webhooks, Extensions, and Realtime publications and warns if the target environment needs manual configuration.
  - **Resiliency**: Implements `session_replication_role = replica` to bypass trigger/constraint conflicts during data load.

### Storage (`storage.py`)
- **API-Based Sync**: Uses the Supabase Storage API for reliable file transfers without direct S3 access.
- **Full Bucket Mirroring**:
  - **Upserting**: Replaces existing files if they've changed.
  - **Wiping**: Deletes files on the remote bucket that are not present in the backup, ensuring a 1:1 mirror.
- **Metadata Preservation**: Preserves file metadata (MIME type, cache control) via sidecar JSON files.
- **Resilient Uploads**: Implements retry logic and memory-buffered transfers to handle network instability.

### Core
- **Deduplication**: Uses **Borg Backup** for efficient, incremental snapshots.
- **Interactive Recovery**: Terminal-based wizard for selecting snapshots and specific components to restore.
- **Auto-Extraction**: Automatically calculates path depths when extracting from Borg to maintain directory structure.
- **Self-Contained**: Manages its own Python virtual environment (`venv`) and local `supabase` CLI.

## Prerequisites
- **Podman** (Docker is NOT supported for security reasons)
- [borgbackup](https://www.borgbackup.org/)
- Python 3.x
- Node.js (for local supabase CLI installation)

## Setup

1.  **Clone and Configure**:
    ```bash
    cp .env.example .env
    ```
2.  **Fill `.env`**:
    - **Source**: `SUPABASE_PROJECT_REF`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SUPABASE_SERVICE_ROLE_KEY`.
    - **Target (Test)**: `TEST_SUPABASE_PROJECT_REF`, `TEST_SUPABASE_DB_PASSWORD`, `TEST_SUPABASE_SERVICE_ROLE_KEY`.
3.  **Install Dependencies**:
    ```bash
    # This will automatically create the venv and install node dependencies
    ./backup.sh
    ```

## Usage

### Backup (Daily Operation)
The `backup.sh` script orchestrates the entire capture flow:
```bash
./backup.sh
```
What it does:
1.  Dumps DB Roles, Schema, and Data.
2.  Captures `supabase_migrations` history.
3.  Diffs `auth` and `storage` schemas.
4.  Downloads all Storage objects and metadata.
5.  Commits the result to a **Borg** archive called `YYYY-MM-DD_HH-MM-SS`.
6.  Prunes archives older than 21 days.

### Restore (Point-in-Time Recovery)
The `restore.sh` script provides two modes:

#### 1. Automated Test Restore
Restores the **latest local backup** to the test environment defined in `.env`.
```bash
./restore.sh --test -y
```

#### 2. Interactive Wizard
Run without arguments to start the interactive selection:
```bash
./restore.sh
```
- **Select Source**: Choose between `Local` files or any historical snapshot from the **Borg** repository.
- **Select Components**: Choose to restore **Database**, **Storage**, or both.
- **Confirm Target**: For non-test projects, you must manually enter the Project Ref and Password to confirm the destructive wipe.

## System Integration (Systemd)

### 1. Enable User Services (Linger)
Allow your user services to run even when you are not logged in:
```bash
sudo loginctl enable-linger $USER
```

### 2. Install Services
User services live in `~/.config/systemd/user/`.
```bash
# Run the installation script
./install.sh

# The script will:
# 1. Create the systemd user directory if it doesn't exist
# 2. Copy service/timer files
# 3. Automatically update paths to matches your current installation directory
# 4. Reload the systemd daemon
# 5. Generate a corrected logrotate configuration file
```

### 3. Enable Automation
```bash
# Enable and start the backup timer (Daily)
systemctl --user enable --now supabase-backup.timer

# Enable and start the restore timer (Weekly)
systemctl --user enable --now supabase-restore.timer
```

### 4. Verification & Logs
Process logs are stored in `logs/` within the project directory.

```bash
# Check timer status
systemctl --user list-timers --all

# Manually trigger backup
systemctl --user start supabase-backup.service

# View Logs
tail -f logs/backup.log
tail -f logs/restore.log
```

**Note on Podman**: This project exclusively uses Podman to allow for secure, rootless operation. The scripts automatically configure the `DOCKER_HOST` environment variable (e.g., `unix:///run/user/1000/podman/podman.sock`). Ensure the `podman.socket` is enabled:
```bash
systemctl --user enable --now podman.socket
```

## Troubleshooting
- **Archive Extraction**: If you manually extract a Borg archive, use `borg extract --strip-components 2`. The `restore.sh` script handles this automatically.
- **Locked Processes**: The system uses `/tmp/supabase_backup_restore.lock` to prevent concurrent operations.
- **Role Errors**: If the restore fails on `GRANT` or `OWNER` statements, ensure `database.py` includes the offending role in its `system_roles` list.
