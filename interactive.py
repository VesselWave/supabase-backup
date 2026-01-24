import json
import subprocess
import sys
import os
from datetime import datetime
from simple_term_menu import TerminalMenu
from dotenv import load_dotenv

# Load environment to get BORG_REPO
load_dotenv()

def calculate_strip_count(borg_json_output):
    """
    Analyzes borg list --json output to determine how many path components to strip.
    Ensures that 'database' and 'storage' directories end up at the root of the extract dir.
    """
    try:
        data = json.loads(borg_json_output)
        paths = [item['path'] for item in data.get('repository', {}).get('items', [])] 
        # borg list --json output structure is: {'archives': [...]} for list archives
        # BUT for list contents: borg list --json REPO::ARCHIVE
        # output is: {'items': [{'path': '...'}, ...]}
        # Let's handle both safely or specific check
        
        if 'items' in data:
            paths = [item['path'] for item in data['items']]
        else:
            return 0
            
        if not paths:
            return 0
            
        # Standard commonpath (no trailing slash)
        common = os.path.commonpath(paths)
        
        if not common or common == '/' or common == '.':
            return 0
            
        # Check if common path ends in database/storage keywords
        # This handles case where backup only contains one folder
        basename = os.path.basename(common)
        if basename in ['database', 'storage', 'data']:
            common = os.path.dirname(common)

        if not common or common == '/' or common == '.':
            return 0
            
        # Count components
        # remove leading slash for counting
        normalized = common.lstrip(os.sep)
        if not normalized:
            return 0
            
        return len(normalized.split(os.sep))
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

def main():
    # If called with --calculate-strip, read JSON from stdin and output count
    if len(sys.argv) > 1 and sys.argv[1] == "--calculate-strip":
        content = sys.stdin.read()
        print(calculate_strip_count(content))
        return

    archives = get_borg_archives()
    
    # 1. Select Backup Source
    # Format options
    # Option 0: Local
    menu_items = ["[Local] Use existing files in LOCAL_BACKUP_DIR (No extraction)"]
    
    # Map menu index to archive object
    archive_map = {}
    
    for idx, arch in enumerate(archives):
        # arch keys: archive, time, id
        # Format time nicely
        try:
            dt = datetime.fromisoformat(arch["time"])
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            time_str = arch["time"]
            
        label = f"[Archive] {time_str} ({arch['name']})"
        menu_items.append(label)
        archive_map[idx + 1] = arch # +1 because of Local option at 0
        
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

    selected_archive = None
    if menu_entry_index > 0:
        selected_archive = archive_map.get(menu_entry_index)
        
    # 2. Select Components (Multi-select)
    components = ["Database", "Storage"]
    # Pre-select both (indexes 0 and 1)
    component_menu = TerminalMenu(
        components,
        title="Select Components to Restore (Space to toggle, Enter to confirm):",
        multi_select=True,
        show_multi_select_hint=True,
        preselected_entries=[0, 1],
        menu_cursor="> ",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
    )
    
    selected_components_indexes = component_menu.show()
    
    if selected_components_indexes is None:
        # If user escapes, careful. Maybe default to nothing or exit?
        # User implies cancel
        sys.exit(1)
        
    restore_db = 0 in selected_components_indexes
    restore_storage = 1 in selected_components_indexes
    
    output = {
        "archive": selected_archive["name"] if selected_archive else "Local",
        "restore_db": restore_db,
        "restore_storage": restore_storage
    }
    
    print(json.dumps(output))

if __name__ == "__main__":
    main()
