import os
import subprocess
import sys
from dotenv import load_dotenv

def run_command(command, env=None):
    """Executes a shell command and exits on failure."""
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=True, env=env)
    if result.returncode != 0:
        print(f"Error: Command failed with return code {result.returncode}")
        return False
    return True

def sync_storage():
    load_dotenv()
    
    project_ref = os.path.expandvars(os.getenv("SUPABASE_PROJECT_REF"))
    access_key = os.path.expandvars(os.getenv("S3_ACCESS_KEY_ID"))
    secret_key = os.path.expandvars(os.getenv("S3_SECRET_ACCESS_KEY"))
    region = os.path.expandvars(os.getenv("S3_REGION", "us-east-1"))
    endpoint = os.path.expandvars(os.getenv("S3_ENDPOINT"))
    local_backup_dir = os.path.expanduser(os.path.expandvars(os.getenv("LOCAL_BACKUP_DIR", "./backups")))
    
    if not all([project_ref, access_key, secret_key, endpoint]):
        print("Error: Missing required S3 configuration in .env")
        sys.exit(1)

    target_dir = os.path.join(local_backup_dir, "storage")
    os.makedirs(target_dir, exist_ok=True)

    # Configure rclone via environment variables.
    # Remote name: supabases3 (avoiding hyphens to ensure simpler mapping)
    rclone_env = os.environ.copy()
    rclone_env["RCLONE_CONFIG_SUPABASES3_TYPE"] = "s3"
    rclone_env["RCLONE_CONFIG_SUPABASES3_PROVIDER"] = "Other"
    rclone_env["RCLONE_CONFIG_SUPABASES3_ACCESS_KEY_ID"] = access_key
    rclone_env["RCLONE_CONFIG_SUPABASES3_SECRET_ACCESS_KEY"] = secret_key
    rclone_env["RCLONE_CONFIG_SUPABASES3_REGION"] = region
    rclone_env["RCLONE_CONFIG_SUPABASES3_ENDPOINT"] = endpoint
    # Also disable config file to avoid permission issues
    rclone_env["RCLONE_CONFIG"] = "/dev/null"

    print(f"Syncing S3 storage for project {project_ref}...")
    
    # Check if rclone is available
    import shutil
    if not shutil.which("rclone"):
        print("Error: 'rclone' CLI not found. Please install rclone.")
        sys.exit(1)
    
    # Use 'supabases3:' as the remote name.
    success = run_command(f"rclone sync supabases3: {target_dir} --progress", env=rclone_env)
    
    if success:
        print(f"Storage sync completed. Files located in: {target_dir}")
    else:
        print("Storage sync failed. Check if your S3 credentials and endpoint are correct.")
        sys.exit(1)

if __name__ == "__main__":
    sync_storage()
