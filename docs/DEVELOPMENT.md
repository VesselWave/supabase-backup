# Developer Guide

Technical details for developers working on the Supabase Backup system.

## Architecture

```
├── backup.sh           # Orchestration: venv setup, run modules, create Borg archive
├── restore.sh          # Orchestration: extraction, interactive selection, restore
├── database.py         # PostgreSQL operations via pg_dump/psql
├── storage.py          # Async HTTP client for Supabase Storage API
├── interactive.py      # Terminal UI using simple-term-menu
├── util.py             # Environment loading, command execution
└── install.sh          # Systemd service deployment with templating
```

**Design Pattern**: Shell scripts orchestrate, Python modules implement business logic.

## Database Module (`database.py`)

### Backup Process

1. **Link**: `supabase link --project-ref {ref}`
2. **Dump**: `pg_dump` for roles/schema/data (separate files)
3. **Migrations**: Capture `supabase_migrations` schema
4. **Diffs**: `supabase db diff` for auth/storage schemas
5. **Timestamp**: Create `.timestamp` file for accurate dating

### Restore Process

**Key Steps**:
1. **Scan Requirements**: Check for Extensions, Webhooks, Realtime usage
2. **Clean Files**: Remove permission-restricted statements
3. **Atomic Wipe**: Drop/recreate schemas, truncate tables
4. **Restore**: Apply cleaned SQL with `session_replication_role = replica`
5. **Apply Diffs**: Restore internal schema changes

**Cleaning Functions**:

- `clean_roles_file()` - Removes `GRANT ... TO "postgres"` (restricted in managed Supabase)
- `clean_schema_file()` - Comments out `ALTER ... OWNER TO "supabase_admin"`
- `clean_data_file()` - Skips system tables like `storage.buckets_vectors`

**Edge Cases Handled**:
- Permission errors on system roles → Regex filtering
- Ownership conflicts → Comment out ALTER statements
- Trigger violations → Use `session_replication_role = replica`
- Schema drift → Capture and reapply diffs
- Vector tables → Skip in data restore

### Adding Cleaning Rules

```python
# Skip new problematic table
def clean_data_file(file_path, output_dir):
    skip_tables = [
        'storage.buckets_vectors',
        'storage.new_system_table',  # Add here
    ]
```

## Storage Module (`storage.py`)

### Architecture

- **Class**: `StorageMigrator` (async context manager)
- **Concurrency**: `asyncio.Semaphore` controls parallel operations
- **Retry Logic**: 3 retries with exponential backoff
- **Security**: Service role key sanitization in errors

### Key Methods

**Backup**: `backup_bucket(bucket_name, target_dir, concurrency=10)`
- Recursively lists files via API
- Downloads concurrently (default: 10 workers)
- Saves metadata as `.meta.json` sidecars

**Restore**: `restore_bucket(bucket_name, source_dir, concurrency=10)`
- Creates bucket if missing
- Uploads files with metadata (memory-buffered)
- Wipes orphaned files not in backup

**Performance**:
- Increase concurrency for high bandwidth (up to 20)
- Decrease for rate-limited APIs (down to 5)
- Files loaded into memory (BytesIO) - not ideal for >100MB files

## Interactive Selector (`interactive.py`)

### Features

- Lists Borg archives sorted by date (newest first)
- Parses timestamps from archive names (`YYYY-MM-DD_HH-MM-SS`)
- Shows dates from LOCAL_BACKUP_DIR and BORG_EXTRACT_DIR
- Multi-select for Database/Storage components
- Outputs JSON for shell consumption

### Key Functions

**Strip Component Calculation**: `calculate_strip_count(borg_json_lines)`
- Finds common path prefix in archive
- Special case: strips to parent if ends with `database`/`storage`/`data`
- Returns depth for `borg extract --strip-components N`

**Date Sources**:
- Archive names parsed as `datetime.strptime(name, "%Y-%m-%d_%H-%M-%S")`
- Local/Extract dates from `database/.timestamp` file (fallback to mtime)

## Orchestration Scripts

### backup.sh

**Key Features**:
- Logging: `exec > >(tee -a "$LOG_FILE") 2>&1`
- Locking: `flock -n 200` on `/tmp/supabase_backup_restore.lock`
- Podman auto-detection: Sets `DOCKER_HOST` for rootless socket
- Workflow: Python modules → Borg create → Borg prune

### restore.sh

**Archive Handling**:
- `Local` → Use LOCAL_BACKUP_DIR as-is
- `Extract` → Use BORG_EXTRACT_DIR as-is
- Archive name → Extract from Borg with calculated strip-components

**Security**:
- Passwords via `read -s` (hidden input)
- Exported as env vars (never CLI args)
- Double confirmation for non-test targets

## Systemd Integration

### Templates

**Pattern**: `install.sh` uses `envsubst` to replace `$INSTALL_DIR` placeholder.

**Workflow**:
1. Detect temporary location (warn user)
2. Optional copy to permanent location (`--copy-to`)
3. Generate services from templates
4. Copy to `~/.config/systemd/user/`
5. Reload systemd daemon

## Development Workflow

### Testing

```bash
# Full cycle
./backup.sh
borg list $HOME/backups/supabase/borg-repo
./restore.sh --test -y

# Interactive
./restore.sh
```

### Debugging

```bash
# Verbose logging
set -x  # Add to top of shell script

# Check archive contents
borg list --json-lines $BORG_REPO::2026-01-27_12-00-00

# Test SQL cleaning interactively
python3 -c "from database import clean_schema_file; \
            clean_schema_file('backup/database/schema.sql', '/tmp')"
```

### Adding Features

**New Backup Component**:
1. Create Python module (e.g., `realtime.py`)
2. Implement `backup()` and `restore()` functions
3. Add calls to `backup.sh` and `restore.sh`
4. Update `interactive.py` components list

## Security

### Credential Handling

- **Environment vars only**: PGPASSWORD, no `--password` flags
- **Sanitization**: Service keys redacted in errors
- **No logging**: Connection strings never logged
- **Process hiding**: Credentials not visible in `ps` output

```python
# storage.py sanitization
def _sanitize_error(self, message: str) -> str:
    return message.replace(self.key, '[REDACTED]')
```

### Rootless Operation

- **Podman required**: No root or `docker` group needed
- **User namespace**: Isolation without privilege escalation
- **Socket**: `unix:///run/user/$UID/podman/podman.sock`

## Performance

### Borg Optimization

- **Deduplication**: Content-defined chunking, 5-20x ratio for daily backups
- **Compression**: Can configure `--compression lz4` (fast) or `--compression zstd,10` (better ratio)

### Storage Tuning

```python
# Adjust based on network
asyncio.run(backup(concurrency=20))  # High bandwidth
asyncio.run(backup(concurrency=5))   # Rate-limited
```

## Common Patterns

### Environment Loading
```python
from dotenv import load_dotenv
load_dotenv()
var = os.getenv("VAR_NAME")
```

### Command Execution
```python
from util import run_command, check_tool
check_tool("borg")
run_command(["borg", "list", repo])
```

### Temporary Files
```python
with tempfile.TemporaryDirectory() as temp_dir:
    cleaned = clean_schema_file(original, temp_dir)
    # Auto-cleanup on exit
```

## Troubleshooting

**Permission errors**: Add roles/tables to skip lists in cleaning functions

**Borg locks**: `borg break-lock $BORG_REPO`

**Storage failures**: Check service role key permissions and bucket policies

**Systemd issues**: `journalctl --user -u supabase-backup.service -f`

## Resources

- [Borg Backup Docs](https://borgbackup.readthedocs.io/)
- [Supabase CLI Reference](https://supabase.com/docs/reference/cli)
- [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
