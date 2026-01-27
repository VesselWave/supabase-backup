# Supabase Backup & Restore System

A production-ready backup and restore solution for Supabase projects. Handles complete database schemas, data, migration history, and storage buckets with deduplication, versioning, and intelligent recovery features.

## Features

- 💾 **Complete Database Backup** - Roles, schemas, data, and migration history
- 🪣 **Storage Mirroring** - Full bucket synchronization with metadata preservation  
- 🖥️ **Interactive Recovery** - Terminal UI for selecting snapshots and components
- ⏰ **Automated Scheduling** - Systemd timers for daily backups and weekly test restores
- 🔧 **Intelligent Patching** - Automatic cleanup of permission-restricted statements
- 🔒 **Security First** - Rootless Podman support, credential sanitization in logs
- 📦 **Deduplication** - Borg Backup reduces storage with incremental snapshots

## Prerequisites

- **Podman** (rootless container runtime)
- **borgbackup** - Deduplication and versioning
- **Python 3.x** - For backup/restore scripts
- **Node.js** - For Supabase CLI

Install on Debian/Ubuntu:
```bash
sudo apt install podman borgbackup python3 python3-venv nodejs npm
```

## Quick Start

### 1. Clone and Configure

```bash
git clone <your-repo-url>
cd supabase-backup

cp .env.example .env
nano .env  # Fill in your Supabase credentials
```

### 2. Configure `.env`

```bash
# Production project (for backups)
SUPABASE_PROJECT_REF="your-project-ref"
SUPABASE_ACCESS_TOKEN="sbp_your_access_token"
SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"

# Test project (for restore testing)
TEST_SUPABASE_PROJECT_REF="your-test-project-ref"
TEST_SUPABASE_DB_PASSWORD="your-test-db-password"
TEST_SUPABASE_SERVICE_ROLE_KEY="your-test-service-role-key"

# Self-hosted Supabase (optional)
# Only needed for self-hosted instances - leave empty for Supabase Cloud
SUPABASE_URL=""              # e.g., http://localhost:8000
TEST_SUPABASE_URL=""         # e.g., https://supabase.mycompany.com
TEST_SUPABASE_DB_HOST=""     # e.g., localhost (if different from API host)

# Backup storage paths
LOCAL_BACKUP_DIR="$HOME/backups/supabase/latest"
BORG_REPO="$HOME/backups/supabase/borg-repo"
BORG_EXTRACT_DIR="$HOME/backups/supabase/extract"
BORG_RETENTION_DAYS="21"
```

**Where to find credentials:**
- `SUPABASE_ACCESS_TOKEN`: [Dashboard → Account → Access Tokens](https://supabase.com/dashboard/account/tokens)
- `SUPABASE_SERVICE_ROLE_KEY`: Project Settings → API → service_role key
- `TEST_SUPABASE_DB_PASSWORD`: Project Settings → Database → Password

### Self-Hosted Supabase Configuration

For self-hosted Supabase instances, add custom URL configuration:

```bash
# Production self-hosted instance
SUPABASE_URL="https://supabase.mycompany.com"
SUPABASE_SERVICE_ROLE_KEY="your-self-hosted-service-role-key"

# Test self-hosted instance  
TEST_SUPABASE_URL="http://localhost:8000"
TEST_SUPABASE_SERVICE_ROLE_KEY="your-test-service-role-key"
TEST_SUPABASE_DB_PASSWORD="your-test-db-password"

# Optional: Override database host if different from API URL
TEST_SUPABASE_DB_HOST="localhost"  # or db.mycompany.com
```

**Notes:**
- When using custom URLs, `SUPABASE_PROJECT_REF` and `TEST_SUPABASE_PROJECT_REF` are optional
- Database host is automatically derived as `db.{url-hostname}` unless `TEST_SUPABASE_DB_HOST` is set
- For Supabase Cloud, leave custom URL variables empty to use standard `https://{project-ref}.supabase.co`

### 3. Run Your First Backup

```bash
./backup.sh
```

This will:
1. Create a Python virtual environment (first run only)
2. Dump your database (schema, data, migrations)
3. Download all storage buckets
4. Create a deduplicated Borg archive named `YYYY-MM-DD_HH-MM-SS`
5. Prune old backups based on retention policy

## Usage

### Manual Backup

```bash
./backup.sh
```

### Manual Restore

#### Interactive Mode (Recommended)

```bash
./restore.sh
```

The interactive wizard lets you:
1. **Select backup source**:
   - `[Local]` - Latest backup in LOCAL_BACKUP_DIR
   - `[Extract]` - Previously extracted Borg archive  
   - `[Archive]` - Any historical Borg snapshot
2. **Choose components**: Database, Storage, or both
3. **Specify target**: Enter project details or use test environment

#### Automated Test Restore

```bash
./restore.sh --test --yes
```

Restores the latest local backup to your test environment without any prompts. Perfect for automated verification.

### View Available Backups

```bash
borg list $HOME/backups/supabase/borg-repo
```

## Automated Backups (Systemd)

### Installation

```bash
# Run the installer
./install.sh

# Enable daily backups
systemctl --user enable --now supabase-backup.timer

# Enable weekly test restores (validates backups work)
systemctl --user enable --now supabase-restore.timer

# Allow services to run when not logged in
sudo loginctl enable-linger $USER
```

### Enable Podman Socket

For systemd services to work with Podman:
```bash
systemctl --user enable --now podman.socket
```

### Monitoring

```bash
# Check timer status
systemctl --user list-timers

# View service logs
journalctl --user -u supabase-backup.service
journalctl --user -u supabase-restore.service

# Check log files
tail -f ~/.config/supabase-backup/logs/backup.log
tail -f ~/.config/supabase-backup/logs/restore.log

# Manually trigger a backup
systemctl --user start supabase-backup.service
```

## Backup Schedule

- **Backups**: Daily with randomization (via systemd timer)
- **Test Restores**: Weekly on Sundays at 2:15 AM
- **Retention**: 21 days by default (configurable via `BORG_RETENTION_DAYS`)

## What Gets Backed Up

### Database
- ✅ All roles and permissions
- ✅ Complete schema (all tables, views, functions, triggers)
- ✅ All data from all tables
- ✅ Migration history (`supabase_migrations` schema)
- ✅ Schema diffs for internal Supabase schemas (auth, storage)

### Storage
- ✅ All files from all buckets
- ✅ File metadata (MIME type, cache control)
- ✅ Bucket configurations

## Configuration Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_PROJECT_REF` | Yes* | Production project reference ID (*not required if using SUPABASE_URL) |
| `SUPABASE_ACCESS_TOKEN` | Yes | Management API token |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Production service role key (bypasses RLS) |
| `SUPABASE_URL` | No | Custom URL for self-hosted production instance (e.g., http://localhost:8000) |
| `TEST_SUPABASE_PROJECT_REF` | For restores* | Test project reference ID (*not required if using TEST_SUPABASE_URL) |
| `TEST_SUPABASE_DB_PASSWORD` | For restores | Test project database password |
| `TEST_SUPABASE_SERVICE_ROLE_KEY` | For restores | Test service role key |
| `TEST_SUPABASE_URL` | No | Custom URL for self-hosted test instance |
| `TEST_SUPABASE_DB_HOST` | No | Custom database host (auto-derived from TEST_SUPABASE_URL if not set) |
| `LOCAL_BACKUP_DIR` | Yes | Directory for raw backup files |
| `BORG_REPO` | Yes | Borg repository location |
| `BORG_EXTRACT_DIR` | Yes | Directory for extracted archives |
| `BORG_RETENTION_DAYS` | No | Days to keep archives (default: 21) |

**Note**: Production database password is NOT needed for backups, only for restores.

## Troubleshooting

### Borg Lock Errors

If a backup was interrupted:
```bash
borg break-lock $HOME/backups/supabase/borg-repo
```

### Podman Socket Not Found

```bash
systemctl --user enable --now podman.socket
export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"
```

### Concurrent Operations

The system uses a lock file (`/tmp/supabase_backup_restore.lock`) to prevent simultaneous backup/restore operations. If you see a "Another process is running" error, wait for the other operation to complete.

### Permission Errors During Restore

The system automatically cleans most permission-related issues. If you encounter errors:
1. Check the restore logs for specific error messages
2. The system skips problematic system tables automatically
3. For persistent issues, see [DEVELOPMENT.md](docs/DEVELOPMENT.md) for customization

### Storage Sync Issues

- Verify your service role key has storage permissions
- Check bucket policies allow the required operations
- Review logs for sanitized error messages

## Security

- **Rootless Containers**: Podman runs without root privileges
- **Credential Protection**: Passwords passed via environment variables, never CLI arguments
- **Log Sanitization**: Service role keys redacted from error messages
- **File Permissions**: Backup files owned by your user only
- **No Production Password**: Backups don't require production database password

## Performance Tips

- **Storage Concurrency**: Adjust with `--concurrency N` flag for storage operations
- **Borg Efficiency**: Automatically deduplicates and compresses, incremental backups only store changes
- **Retention Balance**: Longer retention = more storage, but better recovery options

## Directory Structure

```
$HOME/backups/supabase/
├── latest/              # LOCAL_BACKUP_DIR (working directory)
│   ├── database/
│   │   ├── roles.sql
│   │   ├── schema.sql
│   │   ├── data.sql
│   │   ├── migrations.sql
│   │   ├── diffs/
│   │   └── .timestamp
│   └── storage/
│       └── <bucket>/
│           └── <files>
├── extract/             # BORG_EXTRACT_DIR (for manual extractions)
└── borg-repo/           # BORG_REPO (deduplicated archives)
```

## Documentation

- **[DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Architecture, component deep-dive, and extension guide for developers
- **[STORAGE_MIGRATION_SPEC.md](docs/STORAGE_MIGRATION_SPEC.md)** - Storage API implementation details

## Contributing

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for:
- Architecture overview
- Component implementation details
- Testing workflows
- Adding custom features

## License

MIT License - See LICENSE file

## Support

For issues:
1. Check logs: `~/.config/supabase-backup/logs/`
2. Verify systemd status: `systemctl --user status supabase-backup.service`
3. Test Borg access: `borg list $HOME/backups/supabase/borg-repo`
4. Review [troubleshooting section](#troubleshooting)
