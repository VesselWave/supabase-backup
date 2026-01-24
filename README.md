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
The restore process ensures the target environment **exactly mirrors** the backup. 
- **Database**: The `public` schema is wiped and recreated before restoring data.
- **Storage**: Files on the remote bucket that are not in the backup are deleted (wiped), and missing/changed files are uploaded.

**Warning: Restore operations are destructive. Use with caution.**

#### 1. Restore to Test Environment (Automated)
Uses configuration from `.env` (`TEST_SUPABASE_...` variables).
```bash
./restore.sh --test
```
*Add `-y` or `--yes` to skip the confirmation prompt (useful for automation).*

#### 2. Restore to Custom Target (Manual)
Run without arguments to restore to any project. You will be prompted for the Project Ref, Database Password, and Service Role Key.
```bash
./restore.sh
```
*Requires triple-confirmation to prevent accidental data loss.*

## System Integration (Systemd)

### 1. Logging
Logs are stored in `/var/log/`:
- Backup: `/var/log/supabase-backup.log`
- Restore: `/var/log/supabase-restore.log`

Setup permissions:
```bash
sudo touch /var/log/supabase-backup.log /var/log/supabase-restore.log
sudo chown $USER:$USER /var/log/supabase-backup.log /var/log/supabase-restore.log
```

### 2. Logrotate
```bash
sudo cp systemd/supabase-backup /etc/logrotate.d/supabase-backup
```

### 3. Backup Service & Timer (Daily)
```bash
sudo cp systemd/supabase-backup.service systemd/supabase-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now supabase-backup.timer
```

### 4. Restore Service (On-Demand / Scheduled)
The restore service targets the **Test Environment** defined in `.env`.
```bash
sudo cp systemd/supabase-restore.service /etc/systemd/system/
sudo systemctl daemon-reload
# Run a one-off restore to test:
sudo systemctl start supabase-restore.service
```

## Troubleshooting
- **Permission Denied (Restore)**: The script automatically comments out `GRANT ... TO "postgres"` and skips `storage.buckets_vectors`. If new tables cause issues, add them to `skip_tables` in `database.py`.
- **Wipe Safety**: Manual restores require you to type the target project reference to confirm, ensuring you don't accidentally wipe a wrong project.
