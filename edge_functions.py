"""
Supabase Edge Functions Backup/Restore

Backs up edge functions using `supabase functions download` and restores using `supabase functions deploy`.
"""
import os
import sys
import json
import argparse
import shutil
from dotenv import load_dotenv

from util import run_command, get_env_var, check_tool


def get_supabase_env(project_ref, access_token=None, db_password=None):
    """Prepare environment for Supabase CLI commands."""
    env = os.environ.copy()
    node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
    env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    if access_token:
        env["SUPABASE_ACCESS_TOKEN"] = access_token
    if db_password:
        env["SUPABASE_DB_PASSWORD"] = db_password
    return env


def list_functions(env, project_ref):
    """List all edge functions and return as list of dicts with name and verify_jwt."""
    import subprocess
    
    result = subprocess.run(
        f"supabase functions list --project-ref {project_ref} --output json",
        shell=True, env=env, capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"Error listing functions: {result.stderr}")
        return []
    
    try:
        functions_data = json.loads(result.stdout)
        functions = []
        for func in functions_data:
            functions.append({
                'name': func.get('slug') or func.get('name'),
                'verify_jwt': func.get('verify_jwt', True)
            })
        return functions
    except json.JSONDecodeError as e:
        print(f"Error parsing functions list: {e}")
        return []


def backup():
    """Backup all edge functions to LOCAL_BACKUP_DIR/edge_functions/"""
    project_ref = get_env_var("SUPABASE_PROJECT_REF")
    access_token = get_env_var("SUPABASE_ACCESS_TOKEN", required=False)
    db_password = get_env_var("SUPABASE_DB_PASSWORD", required=False)
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    
    target_dir = os.path.join(local_backup_dir, "edge_functions")
    
    env = get_supabase_env(project_ref, access_token, db_password)
    
    print(f"Starting edge functions backup for project {project_ref}...")
    
    check_tool("supabase", "Error: 'supabase' CLI not found.", path=env["PATH"])
    
    # Link to project
    if not run_command(f"supabase link --project-ref {project_ref}", env=env):
        sys.exit(1)
    
    # List all functions
    functions = list_functions(env, project_ref)
    
    if not functions:
        print("No edge functions found to backup.")
        return
    
    print(f"Found {len(functions)} edge functions to backup")
    
    # Clean target directory
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    
    # Save function metadata (including verify_jwt settings)
    metadata_path = os.path.join(target_dir, "functions_metadata.json")
    with open(metadata_path, 'w') as f:
        json.dump(functions, f, indent=2)
    print(f"Saved function metadata to {metadata_path}")
    
    # Download each function
    # supabase functions download outputs to supabase/functions/<name>/
    supabase_functions_dir = os.path.join(os.getcwd(), "supabase", "functions")
    
    for func in functions:
        name = func['name']
        print(f"Downloading function: {name}")
        
        # Download function
        if not run_command(f"supabase functions download {name}", env=env):
            print(f"Warning: Failed to download function {name}")
            continue
        
        # Move from supabase/functions/<name>/ to backup dir
        src_path = os.path.join(supabase_functions_dir, name)
        dst_path = os.path.join(target_dir, name)
        
        if os.path.exists(src_path):
            shutil.move(src_path, dst_path)
            print(f"  Backed up to {dst_path}")
        else:
            print(f"  Warning: Downloaded function not found at {src_path}")
    
    # Also backup import_map.json if it exists in supabase/functions/
    import_map_src = os.path.join(supabase_functions_dir, "import_map.json")
    if os.path.exists(import_map_src):
        import_map_dst = os.path.join(target_dir, "import_map.json")
        shutil.copy2(import_map_src, import_map_dst)
        print(f"Backed up import_map.json")
    
    # Also check for deno.json / deno.jsonc
    for deno_config in ["deno.json", "deno.jsonc"]:
        config_src = os.path.join(supabase_functions_dir, deno_config)
        if os.path.exists(config_src):
            config_dst = os.path.join(target_dir, deno_config)
            shutil.copy2(config_src, config_dst)
            print(f"Backed up {deno_config}")
    
    print(f"Edge functions backup completed. Files in: {target_dir}")


def restore():
    """Restore edge functions from backup to target project."""
    project_ref = get_env_var("TARGET_PROJECT_REF")
    access_token = get_env_var("SUPABASE_ACCESS_TOKEN", required=False)
    db_password = get_env_var("TARGET_DB_PASSWORD", required=False)
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    
    source_dir = os.path.join(local_backup_dir, "edge_functions")
    
    if not os.path.isdir(source_dir):
        print(f"No edge functions backup found at {source_dir}. Skipping.")
        return
    
    env = get_supabase_env(project_ref, access_token, db_password)
    
    print(f"Starting edge functions restore to project {project_ref}...")
    
    check_tool("supabase", "Error: 'supabase' CLI not found.", path=env["PATH"])
    
    # Link to target project
    if not run_command(f"supabase link --project-ref {project_ref}", env=env):
        sys.exit(1)
    
    # Load function metadata
    metadata_path = os.path.join(source_dir, "functions_metadata.json")
    functions_metadata = {}
    if os.path.exists(metadata_path):
        with open(metadata_path, 'r') as f:
            functions_list = json.load(f)
            functions_metadata = {func['name']: func for func in functions_list}
    
    # Prepare supabase/functions directory for deployment
    supabase_functions_dir = os.path.join(os.getcwd(), "supabase", "functions")
    os.makedirs(supabase_functions_dir, exist_ok=True)
    
    # Copy import_map.json if exists
    import_map_src = os.path.join(source_dir, "import_map.json")
    if os.path.exists(import_map_src):
        import_map_dst = os.path.join(supabase_functions_dir, "import_map.json")
        shutil.copy2(import_map_src, import_map_dst)
        print("Restored import_map.json")
    
    # Copy deno.json/deno.jsonc if exists
    for deno_config in ["deno.json", "deno.jsonc"]:
        config_src = os.path.join(source_dir, deno_config)
        if os.path.exists(config_src):
            config_dst = os.path.join(supabase_functions_dir, deno_config)
            shutil.copy2(config_src, config_dst)
            print(f"Restored {deno_config}")
    
    # Deploy each function
    for item in os.listdir(source_dir):
        item_path = os.path.join(source_dir, item)
        if not os.path.isdir(item_path):
            continue
        
        func_name = item
        print(f"Deploying function: {func_name}")
        
        # Copy function to supabase/functions/
        dst_path = os.path.join(supabase_functions_dir, func_name)
        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        shutil.copytree(item_path, dst_path)
        
        # Build deploy command with original verify_jwt setting
        deploy_cmd = f"supabase functions deploy {func_name}"
        
        func_meta = functions_metadata.get(func_name, {})
        if not func_meta.get('verify_jwt', True):
            deploy_cmd += " --no-verify-jwt"
        
        # Add import map if exists
        import_map_path = os.path.join(supabase_functions_dir, "import_map.json")
        if os.path.exists(import_map_path):
            deploy_cmd += f" --import-map {import_map_path}"
        
        if not run_command(deploy_cmd, env=env):
            print(f"Warning: Failed to deploy function {func_name}")
            continue
        
        print(f"  Deployed {func_name}")
    
    print("Edge functions restore completed.")
    print("\n[!] REMINDER: Edge function secrets are not backed up.")
    print("    You must manually set secrets using: supabase secrets set KEY=VALUE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supabase Edge Functions Backup/Restore")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    parser.add_argument("--env-file", "-e", type=str, default=None, 
                        help="Path to .env file (default: .env in current directory)")
    args = parser.parse_args()

    load_dotenv(dotenv_path=args.env_file)

    if args.action == "backup":
        backup()
    elif args.action == "restore":
        restore()
