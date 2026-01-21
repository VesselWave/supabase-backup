import os
import subprocess
import sys
import argparse
from dotenv import load_dotenv

def run_command(command, env=None, capture=False):
    """Executes a shell command and exits on failure."""
    print(f"Executing: {command}")
    if capture:
        result = subprocess.run(command, shell=True, env=env, capture_output=True, text=True)
    else:
        result = subprocess.run(command, shell=True, env=env)
    
    if result.returncode != 0:
        print(f"Error: Command failed with return code {result.returncode}")
        if capture:
            print(f"Stdout: {result.stdout}")
            print(f"Stderr: {result.stderr}")
        return False
    return True

def get_env_var(key, required=True):
    val = os.path.expandvars(os.getenv(key, ""))
    if required and not val:
        print(f"Error: {key} must be set.")
        sys.exit(1)
    return val

def backup():
    project_ref = get_env_var("SUPABASE_PROJECT_REF")
    access_token = get_env_var("SUPABASE_ACCESS_TOKEN")
    db_password = get_env_var("SUPABASE_DB_PASSWORD", required=False)
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")

    target_dir = os.path.join(local_backup_dir, "database")
    os.makedirs(target_dir, exist_ok=True)

    env = os.environ.copy()
    node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
    env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SUPABASE_ACCESS_TOKEN"] = access_token

    print(f"Starting database dump for project {project_ref}...")

    import shutil
    if not shutil.which("supabase", path=env["PATH"]):
        print("Error: 'supabase' CLI not found. Please run 'npm install supabase' or ensure it is in your PATH.")
        sys.exit(1)

    link_cmd = f"supabase link --project-ref {project_ref}"
    if db_password:
        link_cmd += f" --password '{db_password}'"
    
    if not run_command(link_cmd, env=env):
        sys.exit(1)

    roles_path = os.path.join(target_dir, "roles.sql")
    if not run_command(f"supabase db dump -f {roles_path} --role-only", env=env):
        sys.exit(1)

    schema_path = os.path.join(target_dir, "schema.sql")
    if not run_command(f"supabase db dump -f {schema_path}", env=env):
        sys.exit(1)

    data_path = os.path.join(target_dir, "data.sql")
    if not run_command(f"supabase db dump -f {data_path} --data-only --use-copy", env=env):
        sys.exit(1)

    print(f"Database dump completed. Files located in: {target_dir}")

def restore():
    # Credentials for the TEST database
    db_url = get_env_var("TEST_SUPABASE_DB_URL")
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    
    source_dir = os.path.join(local_backup_dir, "database")
    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)

    print(f"Starting database restore to TEST database...")

    # psql is required
    import shutil
    if not shutil.which("psql"):
        print("Error: 'psql' is required but not installed.")
        sys.exit(1)

    # Order matters: roles -> schema -> data
    files = ["roles.sql", "schema.sql", "data.sql"]
    
    for f in files:
        file_path = os.path.join(source_dir, f)
        if not os.path.exists(file_path):
            print(f"Warning: File {file_path} not found. Skipping.")
            continue
            
        print(f"Restoring {f}...")
        # Use ON_ERROR_STOP=1 to fail fast or remove it to permit loose failures? 
        # Usually for restoration we might want to see errors but continue if possible for roles/duplicates, 
        # but for schema it should probably stop. 
        # For now, let's keep it simple and just run it. 
        # We piped to psql in previous conversations.
        
        # NOTE: data.sql uses COPY which requires input. 
        # roles.sql and schema.sql are normal SQL.
        
        cmd = f"psql \"{db_url}\" -f \"{file_path}\""
        if not run_command(cmd):
            print(f"Warning: Restore of {f} had errors.")
            # We don't exit here because roles might fail if they exist, etc.

    print("Database restore completed.")

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Supabase Database Backup/Restore")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    args = parser.parse_args()

    if args.action == "backup":
        backup()
    elif args.action == "restore":
        restore()
