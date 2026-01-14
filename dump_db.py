import os
import subprocess
import sys
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
        sys.exit(1)
    return result

def backup_db():
    load_dotenv()
    
    project_ref = os.path.expandvars(os.getenv("SUPABASE_PROJECT_REF"))
    access_token = os.path.expandvars(os.getenv("SUPABASE_ACCESS_TOKEN"))
    db_password = os.path.expandvars(os.getenv("SUPABASE_DB_PASSWORD", ""))
    local_backup_dir = os.path.expanduser(os.path.expandvars(os.getenv("LOCAL_BACKUP_DIR", "./backups")))
    
    if not project_ref or not access_token:
        print("Error: SUPABASE_PROJECT_REF and SUPABASE_ACCESS_TOKEN must be set.")
        sys.exit(1)

    # Define dump directory
    target_dir = os.path.join(local_backup_dir, "database")
    os.makedirs(target_dir, exist_ok=True)

    # Ensure supabase CLI is in PATH (assuming it's in node_modules/.bin)
    env = os.environ.copy()
    node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
    env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SUPABASE_ACCESS_TOKEN"] = access_token

    print(f"Starting database dump for project {project_ref}...")

    # Check if supabase CLI is available
    import shutil
    if not shutil.which("supabase", path=env["PATH"]):
        print("Error: 'supabase' CLI not found. Please run 'npm install supabase' or ensure it is in your PATH.")
        sys.exit(1)

    # 1. Link the project
    # Note: --non-interactive is NOT a valid flag for supabase link.
    # We provide the password via --password if available.
    link_cmd = f"supabase link --project-ref {project_ref}"
    if db_password:
        link_cmd += f" --password '{db_password}'"
    
    run_command(link_cmd, env=env)

    # 2. Dump Roles
    roles_path = os.path.join(target_dir, "roles.sql")
    run_command(f"supabase db dump -f {roles_path} --role-only", env=env)

    # 3. Dump Schema
    schema_path = os.path.join(target_dir, "schema.sql")
    run_command(f"supabase db dump -f {schema_path}", env=env)

    # 4. Dump Data (only, using COPY)
    data_path = os.path.join(target_dir, "data.sql")
    run_command(f"supabase db dump -f {data_path} --data-only --use-copy", env=env)

    print(f"Database dump completed. Files located in: {target_dir}")

if __name__ == "__main__":
    backup_db()
