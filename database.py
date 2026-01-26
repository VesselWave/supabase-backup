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
    Returns path to temporary cleaned file.
    """
    if not os.path.exists(file_path):
        return None

    print(f"Cleaning schema file: {file_path}")
    
    import tempfile
    
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', prefix='restore_schema_', delete=False)
    temp_path = tf.name
    
    with open(file_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            if 'OWNER TO "supabase_admin"' in line and not line.strip().startswith("--"):
                tf.write(f"-- {line}")
            else:
                tf.write(line)
    
    tf.close()
    return temp_path

def clean_roles_file(file_path):
    """
    Comments out lines that cause permission errors in roles.sql, specifically
    granting the 'postgres' role which is restricted in managed Supabase.
    Returns path to temporary cleaned file.
    """
    if not os.path.exists(file_path):
        return None

    print(f"Cleaning roles file: {file_path}")
    
    import tempfile
    
    # System roles that should not be created/altered during restore
    # authenticatiod, anon, service_role are default Supabase roles
    # postgres, supabase_admin, dashboard_user, supabase_auth_admin consistute system roles
    # cli_login_postgres seems to be a CLI specific role
    system_roles = [
        "postgres", "anon", "authenticated", "service_role", 
        "supabase_admin", "supabase_auth_admin", "dashboard_user", 
        "gcp_superuser", "gcp_cloudsql_admin", "admin", "root",
        "cli_login_postgres"
    ]
    
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', prefix='restore_roles_', delete=False)
    temp_path = tf.name
    
    with open(file_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            line_strip = line.strip()
            # 1. Check for GRANT <role> TO ...
            if ("GRANT postgres TO" in line or 'GRANT "postgres" TO' in line) and not line_strip.startswith("--"):
                tf.write(f"-- {line}")
            elif ("GRANT supabase_admin TO" in line or 'GRANT "supabase_admin" TO' in line) and not line_strip.startswith("--"):
                tf.write(f"-- {line}")
            # 2. Check for CREATE ROLE <system_role> or ALTER ROLE <system_role>
            else:
                is_system_role_line = False
                for role in system_roles:
                    # Check for "CREATE ROLE "role"" or "CREATE ROLE role"
                    # We utilize a simple check; explicit parsing matches strict quoted/unquoted
                    if (f'CREATE ROLE "{role}"' in line or f"CREATE ROLE {role}" in line or
                        f'ALTER ROLE "{role}"' in line or f"ALTER ROLE {role}" in line):
                        is_system_role_line = True
                        break
                
                if is_system_role_line and not line_strip.startswith("--"):
                     tf.write(f"-- {line}")
                else:
                     tf.write(line)
    
    tf.close()
    return temp_path

def clean_data_file(file_path):
    """
    Comments out COPY statements for tables that cause permission errors.
    Returns path to temporary cleaned file.
    """
    if not os.path.exists(file_path):
        return None

    print(f"Cleaning data file: {file_path}")
    
    import tempfile
    
    # List of tables to skip data restore for if they cause issues
    # "storage"."buckets_vectors" is known to cause permission denied for postgres role
    # "storage"."vector_indexes" is also restricted
    # "supabase_functions"."hooks" schema might not exist on restore target
    skip_tables = ['"storage"."buckets_vectors"', '"storage"."vector_indexes"', '"supabase_functions"."hooks"', '"auth"."flow_state"']
    
    skipping = False
    
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', prefix='restore_data_', delete=False)
    temp_path = tf.name
    
    with open(file_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            if line.startswith("COPY "):
                # Check if this COPY is for a skipped table
                for table in skip_tables:
                    if table in line:
                        skipping = True
                        break
            
            if skipping:
                tf.write(f"-- {line}")
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
                             tf.write(f"-- {line}")
                             skipping_line = True
                             break
                
                if not skipping_line:
                    tf.write(line)
            else:
                tf.write(line)
    
    tf.close()
    return temp_path



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
    
    # Use PGPASSWORD environment variable to avoid password in command line
    db_host = f"db.{project_ref}.supabase.co"
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    source_dir = os.path.join(local_backup_dir, "database")

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)

    print(f"Starting database restore to target database...")

    # psql is required
    check_tool("psql", "Error: 'psql' is required but not installed.")
    
    # Check requirements (Webhooks, Extensions, Realtime)
    check_restore_requirements(source_dir)

    roles_path = os.path.join(source_dir, "roles.sql")
    schema_path = os.path.join(source_dir, "schema.sql")
    data_path = os.path.join(source_dir, "data.sql")
    history_schema_path = os.path.join(source_dir, "history_schema.sql")
    history_data_path = os.path.join(source_dir, "history_data.sql")

    cleaned_files_to_remove = []

    try:
        # 1. Clean schema files
        # We process files and capture their temp paths.
        
        # Helper to process cleaning and track temp file
        def process_clean(original_path, clean_func):
            if os.path.exists(original_path):
                cleaned_path = clean_func(original_path)
                if cleaned_path:
                    cleaned_files_to_remove.append(cleaned_path)
                    return cleaned_path
            return None

        clean_schema_path = process_clean(schema_path, clean_schema_file)
        clean_history_schema_path = process_clean(history_schema_path, clean_schema_file)
        clean_roles_path = process_clean(roles_path, clean_roles_file)
        clean_data_path = process_clean(data_path, clean_data_file)

        # Main Restore
        print("Restoring main database...")
        
        # Use supabase db reset to cleanly wipe the database
        print("Resetting database using Supabase CLI...")
        env = os.environ.copy()
        node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
        env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
        
        # Link to the target project
        link_cmd = f"supabase link --project-ref {project_ref}"
        if not run_command(link_cmd, env=env):
            print("Error: Failed to link to target project.")
            sys.exit(1)
        
        # Reset the database
        reset_cmd = "supabase db reset --linked --yes"
        if not run_command(reset_cmd, env=env):
            print("Error: Database reset failed.")
            sys.exit(1)
        
        print("Database reset completed. Starting restore...")
        
        # Construct command list manually to interleave SET command
        # SET session_replication_role = replica is at the START to prevent triggers during schema/roles
        # Use PGPASSWORD environment variable to avoid password in command line
        psql_env = env.copy()
        psql_env["PGPASSWORD"] = db_password
        
        cmd = ["psql", "--host", db_host, "--port", "5432", "--username", "postgres", "--dbname", "postgres",
               "--single-transaction", "--variable", "ON_ERROR_STOP=1"]
        cmd.extend(["--command", "SET session_replication_role = replica"])
        
        # Truncate storage.buckets to remove any buckets created by migrations during reset
        cmd.extend(["--command", "TRUNCATE TABLE storage.buckets CASCADE"])
        
        # Roles: use cleaned path if available, else original (though logic implies it is always cleaned if exists)
        final_roles_path = clean_roles_path or roles_path
        if os.path.exists(final_roles_path):
            cmd.extend(["--file", final_roles_path])
        else:
            print(f"Warning: {roles_path} not found. Skipping.")

        final_schema_path = clean_schema_path or schema_path
        if os.path.exists(final_schema_path):
            cmd.extend(["--file", final_schema_path])
        else:
            print(f"Warning: {schema_path} not found. Skipping.")

        # Apply auth/storage changes if they exist
        changes_path = os.path.join(source_dir, "changes.sql")
        if os.path.exists(changes_path):
            cmd.extend(["--file", changes_path])
        else:
            print(f"Info: {changes_path} not found. Skipping.")

        final_data_path = clean_data_path or data_path
        if os.path.exists(final_data_path):
            cmd.extend(["--file", final_data_path])
        else:
            print(f"Warning: {data_path} not found. Skipping.")
        
        cmd_str = " ".join([f'"{c}"' if " " in c else c for c in cmd])
        
        if not run_command(cmd_str, env=psql_env):
            print("Error: Main restore failed.")
            sys.exit(1)

        # History Restore
        if os.path.exists(history_schema_path) or os.path.exists(history_data_path):
            print("Restoring migration history...")
            
            # Drop and recreate supabase_migrations schema to avoid conflicts with db reset
            drop_schema_cmd = ["psql", "--host", db_host, "--port", "5432", "--username", "postgres", "--dbname", "postgres",
                              "--command", "DROP SCHEMA IF EXISTS supabase_migrations CASCADE; CREATE SCHEMA supabase_migrations;"]
            drop_schema_str = " ".join([f'"{c}"' if " " in c else c for c in drop_schema_cmd])
            if not run_command(drop_schema_str, env=psql_env):
                print("Warning: Failed to drop/recreate supabase_migrations schema.")
            
            history_cmd = ["psql", "--host", db_host, "--port", "5432", "--username", "postgres", "--dbname", "postgres",
                          "--single-transaction", "--variable", "ON_ERROR_STOP=1"]
            
            final_history_schema_path = clean_history_schema_path or history_schema_path
            if os.path.exists(final_history_schema_path): history_cmd.extend(["--file", final_history_schema_path])
            
            if os.path.exists(history_data_path): history_cmd.extend(["--file", history_data_path])
            
            history_cmd_str = " ".join([f'"{c}"' if " " in c else c for c in history_cmd])
            if not run_command(history_cmd_str, env=psql_env):
                print("Error: History restore failed.")
                sys.exit(1)

        print("Database restore completed.")
        
    finally:
        # Cleanup temporary files
        for f in cleaned_files_to_remove:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError as e:
                    print(f"Warning: Failed to remove temp file {f}: {e}")

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Supabase Database Backup/Restore")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    args = parser.parse_args()

    if args.action == "backup":
        backup()
    elif args.action == "restore":
        restore()
