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
  - **Data Skipping**: smart-skips internal system tables that cause permission errors (e.g., `storage.buckets_vectors`).
  - **Requirements Check**: Scans dumps for usage of Webhooks, Extensions, and Realtime publications and warns if the target environment needs configuration.
  - **Resiliency**: Fallback connection logic using Project Ref/Password if full DB URL is missing.

### Storage (`storage.py`)
- **Full Bucket Sync**: Backs up and restores all storage buckets.
- **Metadata Preservation**: Preserves file metadata (MIME type, cache control) via sidecar JSON files.
- **Resilient Uploads**: Implements retry logic and memory-buffered uploads to handle network instability.

### Core
- **Deduplication**: Uses **Borg Backup** for efficient, incremental snapshots.
- **Retention**: Automatically prunes backups older than 21 days.
- **Self-Contained**: Manages its own Python virtual environment (`venv`) and local `supabase` CLI.

## Prerequisites
- [borgbackup](https://www.borgbackup.org/)
- Python 3
- Node.js (for local supabase CLI installation)

## Setup
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in your credentials in `.env`.
   - **Backup Source**: `SUPABASE_PROJECT_REF`, `SUPABASE_ACCESS_TOKEN`, etc.
   - **Restore Target** (Optional): `TEST_SUPABASE_PROJECT_REF`. Password can be set in `TEST_SUPABASE_DB_PASSWORD` or provided interactively.
3. Install dependencies:
   ```bash
   # This installs the local Supabase CLI and Python venv
   ./backup.sh
   # OR manually:
   npm install supabase@latest
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

## Usage

### Backup (Daily Operation)
The main entry point is `backup.sh`, which orchestrates the entire flow:
```bash
./backup.sh
```
This will:
1. Update local dependencies.
2. Dump database (Roles, Schema, Data, History).
3. Diff system schemas.
4. Download all Storage buckets.
5. Create a new Borg archive.
6. Prune old archives.

### Restore
To restore to a test or production environment defined in your `.env` (under `[Test/Restore Target]`):

1. **Database Restore**:
   ```bash
   # Uses ./venv/bin/python database.py restore
   ./venv/bin/python database.py restore
   ```
   *Automatic Actions:*
   - Checks target requirements (Webhooks/Realtime).
   - Cleans `roles.sql` and `schema.sql` of restricted permissions.
   - Restores main schema/data.
   - Restores migration history.
   *Input*:
   - Prompts for database password if `TEST_SUPABASE_DB_PASSWORD` is not set.

2. **Storage Restore**:
   ```bash
   # Uses ./venv/bin/python storage.py restore
   ./venv/bin/python storage.py restore
   ```
   *Restores all buckets found in the local backup directory.*

## System Integration (Systemd)

### 1. Logging
Logs to `/var/log/supabase-backup.log`:
```bash
sudo touch /var/log/supabase-backup.log
sudo chown $USER:$USER /var/log/supabase-backup.log
```

### 2. Logrotate
```bash
sudo cp systemd/supabase-backup /etc/logrotate.d/supabase-backup
```

### 3. Service & Timer
```bash
sudo cp systemd/supabase-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
# Run daily
sudo systemctl enable --now supabase-backup.timer
```

## Troubleshooting
- **Permission Denied (Restore)**: The script automatically comments out `GRANT ... TO "postgres"` and skips `storage.buckets_vectors`. If new tables cause issues, add them to `skip_tables` in `database.py`.
- **Database Connection**: Ensure `TEST_SUPABASE_PROJECT_REF` is set. The script will ask for the password.
