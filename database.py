import os
import subprocess
import sys
import argparse
from dotenv import load_dotenv

from util import run_command, get_env_var, check_tool

def check_restore_requirements(source_dir):
    """
    Scans the dump files for specific patterns and prints warnings/instructions 
    as per Supabase migration content.
    """
    print("Checking restore requirements...")
    
    files_to_check = ["roles.sql", "schema.sql"]
    found_webhooks = False
    found_extensions = False
    found_realtime = False
    
    for fname in files_to_check:
        fpath = os.path.join(source_dir, fname)
        if not os.path.exists(fpath):
            continue
            
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
            if "pg_net" in content:
                found_webhooks = True
            if "CREATE EXTENSION" in content:
                found_extensions = True
            if "pg_publication" in content or "supabase_realtime" in content:
                found_realtime = True

    if found_webhooks:
        print("\n[REQUIREMENT] Potential Webhooks usage detected.")
        print("  -> Ensure 'Database Webhooks' are enabled in the destination project options if needed.")
        
    if found_extensions:
        print("\n[REQUIREMENT] Extensions detected.")
        print("  -> Ensure any non-default extensions are enabled in the destination project.")

    if found_realtime:
        print("\n[REQUIREMENT] Realtime publication detected.")
        print("  -> Ensure 'Publication' is enabled for the relevant tables in the destination project.")
    print("--------------------------------------------------")

def clean_schema_file(file_path):
    """
    Comments out 'ALTER ... OWNER TO "supabase_admin"' lines to avoid permission errors.
    """
    if not os.path.exists(file_path):
        return

    print(f"Cleaning schema file: {file_path}")
    temp_path = file_path + ".tmp"
    
    with open(file_path, "r", encoding="utf-8") as f_in, \
         open(temp_path, "w", encoding="utf-8") as f_out:
        for line in f_in:
            if 'OWNER TO "supabase_admin"' in line and not line.strip().startswith("--"):
                f_out.write(f"-- {line}")
            else:
                f_out.write(line)
    
    os.replace(temp_path, file_path)

def clean_roles_file(file_path):
    """
    Comments out lines that cause permission errors in roles.sql, specifically
    granting the 'postgres' role which is restricted in managed Supabase.
    """
    if not os.path.exists(file_path):
        return

    print(f"Cleaning roles file: {file_path}")
    temp_path = file_path + ".tmp"
    
    with open(file_path, "r", encoding="utf-8") as f_in, \
         open(temp_path, "w", encoding="utf-8") as f_out:
        for line in f_in:
            # Granting 'postgres' role is restricted
            # Handle both quoted and unquoted variants
            if ("GRANT postgres TO" in line or 'GRANT "postgres" TO' in line) and not line.strip().startswith("--"):
                f_out.write(f"-- {line}")
            # Granting to 'supabase_admin' might also fail depending on context, but postgres is the main one seen.
            elif ("GRANT supabase_admin TO" in line or 'GRANT "supabase_admin" TO' in line) and not line.strip().startswith("--"):
                f_out.write(f"-- {line}")
            else:
                f_out.write(line)
    
    os.replace(temp_path, file_path)

def clean_data_file(file_path):
    """
    Comments out COPY statements for tables that cause permission errors.
    """
    if not os.path.exists(file_path):
        return

    print(f"Cleaning data file: {file_path}")
    temp_path = file_path + ".tmp"
    
    # List of tables to skip data restore for if they cause issues
    # "storage"."buckets_vectors" is known to cause permission denied for postgres role
    # "storage"."vector_indexes" is also restricted
    # "supabase_functions"."hooks" schema might not exist on restore target
    skip_tables = ['"storage"."buckets_vectors"', '"storage"."vector_indexes"', '"supabase_functions"."hooks"']
    
    skipping = False
    
    with open(file_path, "r", encoding="utf-8") as f_in, \
         open(temp_path, "w", encoding="utf-8") as f_out:
        for line in f_in:
            if line.startswith("COPY "):
                # Check if this COPY is for a skipped table
                for table in skip_tables:
                    if table in line:
                        skipping = True
                        break
            
            if skipping:
                f_out.write(f"-- {line}")
                if line.strip() == r"\.":
                    skipping = False
            elif "pg_catalog.setval" in line:
                skipping_line = False
                # Check if setval is for a skipped table's sequence
                for table in skip_tables:
                    parts = table.split('.')
                    if len(parts) > 0:
                        schema_part = parts[0] 
                        if schema_part in line:
                             f_out.write(f"-- {line}")
                             skipping_line = True
                             break
                
                if not skipping_line:
                    f_out.write(line)
            else:
                f_out.write(line)
    
    os.replace(temp_path, file_path)

def wipe_database(db_url):
    """
    Drops and recreates the public schema to ensure a clean state before restore.
    """
    print("Wiping 'public' schema on target database...")
    
    # We verify the connection and wipe public schema
    # Use psql for this as it's already available
    
    commands = [
        "DROP SCHEMA IF EXISTS public CASCADE;",
        "CREATE SCHEMA public;",
        "GRANT ALL ON SCHEMA public TO postgres;",
        "GRANT ALL ON SCHEMA public TO public;" 
    ]
    
    query = " ".join(commands)
    
    # Construct psql command
    cmd = ["psql", "--dbname", db_url, "--command", query]
    
    cmd_str = " ".join([f'"{c}"' if " " in c or c.startswith("postgresql://") else c for c in cmd])
    
    if not run_command(cmd_str):
        print("Error: Database wipe failed.")
        sys.exit(1)
    
    print("Public schema wiped and recreated successfully.")

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

    # Dump migration history
    print("Dumping migration history...")
    history_schema_path = os.path.join(target_dir, "history_schema.sql")
    history_data_path = os.path.join(target_dir, "history_data.sql")
    
    run_command(f"supabase db dump -f {history_schema_path} --schema supabase_migrations", env=env)
    run_command(f"supabase db dump -f {history_data_path} --use-copy --data-only --schema supabase_migrations", env=env)

    # Capture changes in auth and storage schemas
    print("Capturing auth and storage schema changes...")
    changes_path = os.path.join(target_dir, "changes.sql")
    # Using shell redirection for the output
    # run_command uses shell=True so redirection works
    run_command(f"supabase db diff --linked --schema auth,storage > {changes_path}", env=env)

def restore():
    # Credentials for the TARGET database
    project_ref = get_env_var("TARGET_PROJECT_REF")
    db_password = get_env_var("TARGET_DB_PASSWORD")
    
    import urllib.parse
    encoded_password = urllib.parse.quote_plus(db_password)
    db_url = f"postgresql://postgres:{encoded_password}@db.{project_ref}.supabase.co:5432/postgres"
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    source_dir = os.path.join(local_backup_dir, "database")

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)

    print(f"Starting database restore to target database...")

    # psql is required
    check_tool("psql", "Error: 'psql' is required but not installed.")
    
    # 0. WIPE DATABASE
    wipe_database(db_url)

    # 0. Check requirements (Webhooks, Extensions, Realtime)
    check_restore_requirements(source_dir)

    roles_path = os.path.join(source_dir, "roles.sql")
    schema_path = os.path.join(source_dir, "schema.sql")
    data_path = os.path.join(source_dir, "data.sql")
    history_schema_path = os.path.join(source_dir, "history_schema.sql")
    history_data_path = os.path.join(source_dir, "history_data.sql")

    # 1. Clean schema files
    clean_schema_file(schema_path)
    clean_schema_file(history_schema_path)
    clean_roles_file(roles_path)
    clean_data_file(data_path)

    # Main Restore
    print("Restoring main database...")
    
    # Construct command list manually to interleave SET command
    cmd = ["psql", "--single-transaction", "--variable", "ON_ERROR_STOP=1"]
    
    if os.path.exists(roles_path):
        cmd.extend(["--file", roles_path])
    else:
        print(f"Warning: {roles_path} not found. Skipping.")

    if os.path.exists(schema_path):
        cmd.extend(["--file", schema_path])
    else:
        print(f"Warning: {schema_path} not found. Skipping.")

    cmd.extend(["--command", "SET session_replication_role = replica"])

    if os.path.exists(data_path):
        cmd.extend(["--file", data_path])
    else:
        print(f"Warning: {data_path} not found. Skipping.")

    cmd.extend(["--dbname", db_url])
    
    cmd_str = " ".join([f'"{c}"' if " " in c or c.startswith("postgresql://") else c for c in cmd])
    
    if not run_command(cmd_str):
        print("Error: Main restore failed.")
        sys.exit(1)

    # History Restore
    if os.path.exists(history_schema_path) or os.path.exists(history_data_path):
        print("Restoring migration history...")
        history_cmd = ["psql", "--single-transaction", "--variable", "ON_ERROR_STOP=1"]
        if os.path.exists(history_schema_path): history_cmd.extend(["--file", history_schema_path])
        if os.path.exists(history_data_path): history_cmd.extend(["--file", history_data_path])
        history_cmd.extend(["--dbname", db_url])
        
        history_cmd_str = " ".join([f'"{c}"' if " " in c or c.startswith("postgresql://") else c for c in history_cmd])
        if not run_command(history_cmd_str):
            print("Error: History restore failed.")
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
