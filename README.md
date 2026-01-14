# Supabase Backup System

This project provides a robust backup solution for Supabase, including database downloads and S3 storage synchronization, with historical versioning via Borg Backup.

## Features
- **Database Backups**: Performs full database dumps (roles, schema, and data) using the **Supabase CLI**.
- **Storage Sync**: Uses `rclone` (via Python) to sync all S3 buckets.
- **Deduplicated Backups**: Uses `Borg Backup` to store versioned archives efficiently.
- **Retention**: Automatically prunes backups older than 21 days.
- **No Passphrase**: Borg is initialized without encryption by default for ease of automation.
- **Self-contained**: Uses a Python virtual environment (`venv`) and local `supabase` CLI installation.

## Prerequisites
Ensure the following are installed on your system:
- [rclone](https://rclone.org/)
- [borgbackup](https://www.borgbackup.org/)
- Python 3
- Node.js (for local supabase CLI)

## Setup
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in your credentials in `.env` (Access Token, S3 keys, etc.).
3. Install dependencies:
   ```bash
   npm install supabase
   # The backup script will automatically set up the Python venv
   ```

## System Integration (Systemd + Logrotate)

The backup system can be integrated as a system-level service for automated daily backups and standardized logging.

### 1. Logging Setup
The service logs to `/var/log/supabase-backup.log`. Set it up with:
```bash
sudo touch /var/log/supabase-backup.log
sudo chown $USER:$USER /var/log/supabase-backup.log
```

### 2. Logrotate Configuration
Manage log growth and compression:
```bash
sudo cp systemd/supabase-backup /etc/logrotate.d/supabase-backup
```

### 3. Systemd Units
Install the service and timer:
```bash
# Copy units
sudo cp systemd/supabase-backup.* /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now supabase-backup.timer
```

### 4. Monitoring & Management
- **View Logs**: `tail -f /var/log/supabase-backup.log` or `journalctl -u supabase-backup.service`
- **Check Timer**: `systemctl list-timers --all | grep supabase`
- **Manual Run**: `sudo systemctl start supabase-backup.service`

## Structure
- `backup.sh`: Main orchestration script.
- `dump_db.py`: Executes `supabase db dump` for roles, schema, and data.
- `sync_storage.py`: Handles S3 synchronization using rclone.
- `systemd/`: Contains system integration configurations: `supabase-backup.service`, `supabase-backup.timer`, and logrotate config.
- `venv/`: Python virtual environment.
- `node_modules/`: Contains the local `supabase` CLI.
- `backups/`: Location for the latest raw files (database/ and storage/) before archiving.
- `borg-repo/`: The versioned backup repository.
