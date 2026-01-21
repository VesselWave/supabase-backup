import os
import subprocess
import sys
import argparse
from dotenv import load_dotenv

def run_command(command, env=None):
    """Executes a shell command and exits on failure."""
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=True, env=env)
    if result.returncode != 0:
        print(f"Error: Command failed with return code {result.returncode}")
        return False
    return True

def get_env_var(key, required=True):
    val = os.path.expandvars(os.getenv(key, ""))
    if required and not val:
        print(f"Error: {key} must be set.")
        sys.exit(1)
    return val

def check_rclone():
    import shutil
    if not shutil.which("rclone"):
        print("Error: 'rclone' CLI not found. Please install rclone.")
        sys.exit(1)

def backup():
    project_ref = get_env_var("SUPABASE_PROJECT_REF")
    access_key = get_env_var("S3_ACCESS_KEY_ID")
    secret_key = get_env_var("S3_SECRET_ACCESS_KEY")
    region = get_env_var("S3_REGION", required=False) or "us-east-1"
    endpoint = get_env_var("S3_ENDPOINT")
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")

    target_dir = os.path.join(local_backup_dir, "storage")
    os.makedirs(target_dir, exist_ok=True)

    # Configure rclone env for backup (S3 -> Local)
    rclone_env = os.environ.copy()
    rclone_env["RCLONE_CONFIG_SUPABASES3_TYPE"] = "s3"
    rclone_env["RCLONE_CONFIG_SUPABASES3_PROVIDER"] = "Other"
    rclone_env["RCLONE_CONFIG_SUPABASES3_ACCESS_KEY_ID"] = access_key
    rclone_env["RCLONE_CONFIG_SUPABASES3_SECRET_ACCESS_KEY"] = secret_key
    rclone_env["RCLONE_CONFIG_SUPABASES3_REGION"] = region
    rclone_env["RCLONE_CONFIG_SUPABASES3_ENDPOINT"] = endpoint
    rclone_env["RCLONE_CONFIG"] = "/dev/null"

    print(f"Syncing S3 storage for project {project_ref}...")
    check_rclone()
    
    # Sync from S3 (remote) to Local
    if not run_command(f"rclone sync supabases3: {target_dir} --progress", env=rclone_env):
        sys.exit(1)

    print(f"Storage sync completed. Files located in: {target_dir}")

def restore():
    # Credentials for the TEST S3
    access_key = get_env_var("TEST_S3_ACCESS_KEY_ID")
    secret_key = get_env_var("TEST_S3_SECRET_ACCESS_KEY")
    region = get_env_var("TEST_S3_REGION", required=False) or "auto"
    endpoint = get_env_var("TEST_S3_ENDPOINT")
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    source_dir = os.path.join(local_backup_dir, "storage")

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)
        
    print(f"Syncing local storage to TEST S3 (tests3:TEST)...")
    check_rclone()

    # Configure rclone env for restore (Local -> TEST S3)
    rclone_env = os.environ.copy()
    rclone_env["RCLONE_CONFIG_TESTS3_TYPE"] = "s3"
    rclone_env["RCLONE_CONFIG_TESTS3_PROVIDER"] = "Other"
    rclone_env["RCLONE_CONFIG_TESTS3_ACCESS_KEY_ID"] = access_key
    rclone_env["RCLONE_CONFIG_TESTS3_SECRET_ACCESS_KEY"] = secret_key
    rclone_env["RCLONE_CONFIG_TESTS3_REGION"] = region
    rclone_env["RCLONE_CONFIG_TESTS3_ENDPOINT"] = endpoint
    rclone_env["RCLONE_CONFIG"] = "/dev/null"

    # Sync from Local to TEST S3 (remote)
    # Using 'tests3:' as remote alias
    # Iterate over directories in source_dir (which correspond to buckets)
    for item in os.listdir(source_dir):
        bucket_source_path = os.path.join(source_dir, item)
        if os.path.isdir(bucket_source_path):
            bucket_name = item
            # Dest: tests3:bucket_name
            dest_path = f"tests3:{bucket_name}"
            
            print(f"Syncing bucket '{bucket_name}' to '{dest_path}'...")
            if not run_command(f"rclone sync {bucket_source_path} {dest_path} --progress", env=rclone_env):
                sys.exit(1)
        
    print("Storage restore completed.")

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Supabase Storage Backup/Restore")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    args = parser.parse_args()

    if args.action == "backup":
        backup()
    elif args.action == "restore":
        restore()
