import json
import subprocess
import sys
import os
from datetime import datetime
from simple_term_menu import TerminalMenu
from dotenv import load_dotenv

# Load environment to get BORG_REPO
load_dotenv()

def calculate_strip_count(borg_json_lines):
    """
    Determines how many path components to strip from Borg extraction.
    Accepts string input (multiple JSON lines).
    """
    try:
        paths = []
        for line in borg_json_lines.strip().split('\n'):
            if not line: continue
            try:
                data = json.loads(line)
                if 'path' in data: paths.append(data['path'])
            except json.JSONDecodeError: continue

        if not paths: return 0
            
        common = os.path.commonpath(paths)
        if not common or common in ['.', '/']: return 0
            
        parts = common.strip(os.sep).split(os.sep)
        # If deepest common part is database/storage/data, strip to its parent
        if parts[-1] in ['database', 'storage', 'data']:
            return len(parts) - 1
            
        return len(parts)
    except Exception:
        return 0

def get_borg_archives():
    repo = os.getenv("BORG_REPO", "./borg-repo")
    if not os.path.exists(repo):
        return []

    try:
        # Borg needs credentials if encryption is used, but we deal with unencrypted for now or env vars
        # Check backup.sh: export BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes
        env = os.environ.copy()
        env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
        env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
        
        result = subprocess.run(
            ["borg", "list", "--json", repo], 
            capture_output=True, 
            text=True, 
            env=env
        )
        
        if result.returncode != 0:
            # If borg fails (e.g. lock), we might return empty or raise
            return []
            
        data = json.loads(result.stdout)
        archives = data.get("archives", [])
        # Sort by time, newest first
        archives.sort(key=lambda x: x["time"], reverse=True)
        return archives
    except Exception as e:
        # Fallback if borg not installed or other error
        return []

def get_local_backup_date():
    """Get the backup date from LOCAL_BACKUP_DIR by checking database/.timestamp file"""
    local_dir = os.getenv("LOCAL_BACKUP_DIR", "./backups")
    local_dir = os.path.expanduser(os.path.expandvars(local_dir))
    
    timestamp_file = os.path.join(local_dir, "database", ".timestamp")
    if os.path.exists(timestamp_file):
        try:
            with open(timestamp_file, 'r') as f:
                timestamp = f.read().strip()
                dt = datetime.fromisoformat(timestamp)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    
    # Fallback: check directory modification time
    if os.path.exists(local_dir):
        try:
            mtime = os.path.getmtime(local_dir)
            dt = datetime.fromtimestamp(mtime)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    
    return "Unknown Date"

def get_extract_backup_date():
    """Get the backup date from BORG_EXTRACT_DIR by checking database/.timestamp file"""
    extract_dir = os.getenv("BORG_EXTRACT_DIR")
    if not extract_dir:
        return None
    
    extract_dir = os.path.expanduser(os.path.expandvars(extract_dir))
    
    if not os.path.exists(extract_dir):
        return None
    
    timestamp_file = os.path.join(extract_dir, "database", ".timestamp")
    if os.path.exists(timestamp_file):
        try:
            with open(timestamp_file, 'r') as f:
                timestamp = f.read().strip()
                dt = datetime.fromisoformat(timestamp)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    
    # Fallback: check directory modification time
    try:
        mtime = os.path.getmtime(extract_dir)
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    
    return "Unknown Date"

def main():
    # If called with --calculate-strip, determine output count via stdin
    if "--calculate-strip" in sys.argv:
        print(calculate_strip_count(sys.stdin.read()))
        return

    archives = get_borg_archives()
    
    # 1. Select Backup Source
    # Format options
    menu_items = []
    
    # Map menu index to special string values or archive objects
    archive_map = {}
    current_index = 0
    
    # Option 0: Local (with date from LOCAL_BACKUP_DIR)
    local_date = get_local_backup_date()
    menu_items.append(f"[Local] {local_date} - Use existing files in LOCAL_BACKUP_DIR")
    archive_map[current_index] = "Local"
    current_index += 1
    
    # Option 1: Extract (with date from BORG_EXTRACT_DIR if exists)
    extract_date = get_extract_backup_date()
    if extract_date:
        menu_items.append(f"[Extract] {extract_date} - Use last extracted from BORG_EXTRACT_DIR")
        archive_map[current_index] = "Extract"
        current_index += 1
    
    # Borg archives
    for idx, arch in enumerate(archives):
        # Parse the timestamp from the archive name (format: YYYY-MM-DD_HH-MM-SS)
        archive_name = arch['name']
        try:
            # Try to parse the archive name as a timestamp
            time_str = archive_name.replace('_', ' ').replace('-', '-', 2).replace('-', ':', 2)
            # Expected format after replacement: "YYYY-MM-DD HH:MM:SS"
            dt = datetime.strptime(archive_name, "%Y-%m-%d_%H-%M-%S")
            time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            # Fallback to Borg metadata time if name doesn't match expected format
            try:
                dt = datetime.fromisoformat(arch["time"])
                time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_display = arch["time"]
            
        label = f"[Archive] {time_display} - {archive_name}"
        menu_items.append(label)
        archive_map[current_index] = arch
        current_index += 1
        
    if not archives:
        menu_items.append("[Info] No Borg archives found (Is Borg initialized?)")

    terminal_menu = TerminalMenu(
        menu_items,
        title="Select Backup Source:",
        menu_cursor="> ",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
        cycle_cursor=True,
        clear_screen=False,
    )
    
    menu_entry_index = terminal_menu.show()
    
    if menu_entry_index is None:
        # User cancelled
        sys.exit(1)

    selection = archive_map.get(menu_entry_index)
    
    # Determine the archive name to use
    archive_name = "Local"
    if isinstance(selection, dict):
        # It's a Borg archive
        archive_name = selection["name"]
    elif isinstance(selection, str):
        # It's "Local" or "Extract"
        archive_name = selection
        
    # 2. Select Components (Multi-select)
    components = ["Database", "Edge Functions", "Storage"]
    # Pre-select all (indexes 0, 1, 2)
    component_menu = TerminalMenu(
        components,
        title="Select Components to Restore (Space to toggle, Enter to confirm):",
        multi_select=True,
        show_multi_select_hint=True,
        preselected_entries=[0, 1, 2],
        multi_select_select_on_accept=False,
        multi_select_empty_ok=True,
        menu_cursor="> ",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
    )
    
    selected_components_indexes = component_menu.show()
    
    if selected_components_indexes is None:
        # User cancelled
        sys.exit(1)
        
    restore_db = 0 in selected_components_indexes
    restore_edge_functions = 1 in selected_components_indexes
    restore_storage = 2 in selected_components_indexes
    
    output = {
        "archive": archive_name,
        "restore_db": restore_db,
        "restore_edge_functions": restore_edge_functions,
        "restore_storage": restore_storage
    }
    
    print(json.dumps(output))

if __name__ == "__main__":
    main()
