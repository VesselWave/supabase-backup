import os
import subprocess
import sys
import argparse
from dotenv import load_dotenv

from util import run_command, get_env_var, check_tool

def backup():
    project_ref = get_env_var("SUPABASE_PROJECT_REF")
    access_token = get_env_var("SUPABASE_ACCESS_TOKEN", required=False)
    db_password = get_env_var("SUPABASE_DB_PASSWORD", required=False)
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")

    target_dir = os.path.join(local_backup_dir, "database")
    os.makedirs(target_dir, exist_ok=True)

    env = os.environ.copy()
    node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
    env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    if access_token:
        env["SUPABASE_ACCESS_TOKEN"] = access_token

    print(f"Starting database dump for project {project_ref}...")

    check_tool("supabase", "Error: 'supabase' CLI not found. Please run 'npm install supabase' or ensure it is in your PATH.", path=env["PATH"])

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
    check_tool("psql", "Error: 'psql' is required but not installed.")

    roles_path = os.path.join(source_dir, "roles.sql")
    schema_path = os.path.join(source_dir, "schema.sql")
    data_path = os.path.join(source_dir, "data.sql")
    
    # Construct the single transaction command as per Supabase docs
    # psql \
    #   --single-transaction \
    #   --variable ON_ERROR_STOP=1 \
    #   --file roles.sql \
    #   --file schema.sql \
    #   --command 'SET session_replication_role = replica' \
    #   --file data.sql \
    #   --dbname [CONNECTION_STRING]

    cmd = ["psql", "--single-transaction", "--variable", "ON_ERROR_STOP=1"]
    
    if os.path.exists(roles_path):
        cmd.extend(["--file", roles_path])
    else:
        print(f"Warning: {roles_path} not found. Skipping.")

    if os.path.exists(schema_path):
        cmd.extend(["--file", schema_path])
    else:
        print(f"Warning: {schema_path} not found. Skipping.")

    # Set session_replication_role = replica to disable triggers/FK checks during data load
    cmd.extend(["--command", "SET session_replication_role = replica"])

    if os.path.exists(data_path):
        cmd.extend(["--file", data_path])
    else:
        print(f"Warning: {data_path} not found. Skipping.")

    cmd.extend(["--dbname", db_url])

    print("Executing restore command...")
    # Masking DB URL for print if needed, but run_command prints it. 
    # run_command will print the command, so be careful with passwords. 
    # Current run_command implementation just runs shell=True. 
    # We should probably pass list to subprocess.run for safety if we change util, 
    # but existing util.run_command expects string for shell=True usually?
    # util.run_command: subprocess.run(command, shell=True...)
    # So we need to join the list into a string.
    
    cmd_str = " ".join([f'"{c}"' if " " in c or c.startswith("postgresql://") else c for c in cmd])
    
    if not run_command(cmd_str):
        print("Error: Restore failed.")
        sys.exit(1)

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
