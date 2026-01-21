import os
import subprocess
import sys
import argparse
import boto3
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

from util import run_command, get_env_var, check_tool, get_db_url

import json

def get_storage_mapping(db_url=None):
    """Queries the database for storage object mapping. Fallback to supabase CLI if db_url is missing."""
    sql = "SELECT bucket_id, name, id, (metadata->>'mimetype') as mimetype FROM storage.objects;"
    
    if db_url:
        cmd = ["psql", db_url, "-c", sql, "-A", "-t", "-F", ","]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            mapping = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(',')
                    if len(parts) >= 3:
                        mapping.append({
                            'bucket_id': parts[0],
                            'name': parts[1],
                            'id': parts[2],
                            'mimetype': parts[3] if len(parts) > 3 else None
                        })
            return mapping
        else:
            print(f"Warning: psql query failed: {result.stderr}")

    # Fallback or alternative: Use Supabase CLI
    print("Trying to fetch mapping via Supabase CLI...")
    env = os.environ.copy()
    node_bin = os.path.join(os.getcwd(), "node_modules", ".bin")
    env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    
    cmd = ["supabase", "db", "query", sql, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    
    if result.returncode == 0:
        try:
            return json.loads(result.stdout)
        except Exception as e:
            print(f"Error parsing CLI JSON: {e}")
            return []
    else:
        print(f"Error querying DB via CLI: {result.stderr}")
        return []

def download_item(s3, item, target_dir):
    bucket = item['bucket_id']
    name = item['name']
    obj_id = item['id']
    
    local_path = os.path.join(target_dir, bucket, obj_id)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    
    try:
        s3.download_file(bucket, name, local_path)
        return "downloaded"
    except Exception as e:
        return f"error: {e}"

def upload_item(s3, item, source_dir):
    bucket = item['bucket_id']
    name = item['name'] # Original name (not used for key)
    obj_id = item['id'] # Key used in S3
    mimetype = item['mimetype'] or 'image/jpeg'
    
    if mimetype == 'image/jpg':
        mimetype = 'image/jpeg'
        
    local_path = os.path.join(source_dir, bucket, obj_id)
    if not os.path.exists(local_path):
        # Fallback to logical name if UUID not found (for legacy backups)
        local_path = os.path.join(source_dir, bucket, name)
        
    if os.path.exists(local_path):
        try:
            s3.upload_file(
                local_path, 
                bucket, 
                obj_id,
                ExtraArgs={'ContentType': mimetype}
            )
            return "uploaded"
        except Exception as e:
            return f"error: {e}"
    else:
        return "missing"

def backup():
    db_url = get_db_url(required=False)
    
    access_key = get_env_var("S3_ACCESS_KEY_ID")
    secret_key = get_env_var("S3_SECRET_ACCESS_KEY")
    region = get_env_var("S3_REGION", required=False) or "us-east-1"
    endpoint = get_env_var("S3_ENDPOINT")
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    target_dir = os.path.join(local_backup_dir, "storage")
    os.makedirs(target_dir, exist_ok=True)

    print(f"Fetching storage mapping from database...")
    mapping = get_storage_mapping(db_url)
    if not mapping:
        print("No storage objects found or error querying database.")
        return

    print(f"Downloading {len(mapping)} objects from S3...")
    s3 = boto3.client(
        service_name='s3',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint,
        region_name=region
    )

    results = {"downloaded": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(download_item, s3, item, target_dir) for item in mapping]
        for future in futures:
            res = future.result()
            if res.startswith("error"):
                results["error"] += 1
            else:
                results[res] += 1

    print(f"Storage backup completed. Downloaded: {results['downloaded']}, Errors: {results['error']}")

def restore():
    db_url = get_db_url(prefix="TEST_")
    
    access_key = get_env_var("TEST_S3_ACCESS_KEY_ID")
    secret_key = get_env_var("TEST_S3_SECRET_ACCESS_KEY")
    region = get_env_var("TEST_S3_REGION", required=False) or "us-east-1"
    endpoint = get_env_var("TEST_S3_ENDPOINT")
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    source_dir = os.path.join(local_backup_dir, "storage")

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)

    print(f"Fetching storage mapping from TEST database...")
    mapping = get_storage_mapping(db_url)
    if not mapping:
        print("No storage objects found in TEST database. Ensure database restore happened first.")
        return

    print(f"Uploading {len(mapping)} objects to TEST S3...")
    s3 = boto3.client(
        service_name='s3',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint,
        region_name=region
    )

    results = {"uploaded": 0, "missing": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(upload_item, s3, item, source_dir) for item in mapping]
        for future in futures:
            res = future.result()
            if res.startswith("error"):
                results["error"] += 1
            else:
                results[res] += 1

    print(f"Storage restore completed. Uploaded: {results['uploaded']}, Missing: {results['missing']}, Errors: {results['error']}")

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Supabase Storage Backup/Restore")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    args = parser.parse_args()

    if args.action == "backup":
        backup()
    elif args.action == "restore":
        restore()
