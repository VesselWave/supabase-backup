import asyncio
import json
import os
import sys
import argparse
import aiohttp
from typing import List, Dict, Any, Optional
from urllib.parse import quote
import tqdm
from dotenv import load_dotenv

from util import get_env_var

def _should_show_progress() -> bool:
    """Check if we should show progress bars (interactive terminal)."""
    return sys.stderr.isatty()

class StorageMigrator:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip('/')
        self.key = key
        
        self.headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key
        }
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _sanitize_error(self, message: str) -> str:
        """Sanitize error messages to prevent service role key exposure."""
        import re
        # Broadly catch JWTs: Header.Payload.Signature (skipping content checks for speed/safety)
        # eyJ... starts the header.
        sanitized = re.sub(r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', '<REDACTED_JWT>', message)
        
        # Also mask any occurrence of the actual key if it's not a standard JWT (e.g. plain opaque token)
        if self.key:
           sanitized = sanitized.replace(self.key, '<REDACTED_KEY>')
        return sanitized

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _request_with_retry(self, method: str, url: str, retries: int = 3, **kwargs) -> aiohttp.ClientResponse:
        session = await self._get_session()
        last_exception = None
        
        for attempt in range(retries):
            try:
                resp = await session.request(method, url, **kwargs)
                if resp.status >= 500 or resp.status == 429:
                    if attempt < retries - 1:
                        await asyncio.sleep(1 * (attempt + 1))
                        continue
                return resp
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt < retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise last_exception
        return resp

    async def list_buckets(self) -> List[Dict[str, Any]]:
        resp = await self._request_with_retry("GET", f"{self.url}/storage/v1/bucket", headers=self.headers)
        async with resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Failed to list buckets: {resp.status} - {self._sanitize_error(text)}")
            return await resp.json()

    async def create_bucket_if_missing(self, bucket: Dict[str, Any]):
        current_buckets = await self.list_buckets()
        if any(b['name'] == bucket['name'] for b in current_buckets):
            return

        payload = {
            "id": bucket['name'],
            "name": bucket['name'],
            "public": bucket.get('public', False),
            "file_size_limit": bucket.get('file_size_limit'),
            "allowed_mime_types": bucket.get('allowed_mime_types')
        }
        resp = await self._request_with_retry("POST", f"{self.url}/storage/v1/bucket", headers=self.headers, json=payload)
        async with resp:
            if resp.status not in [200, 201, 400, 409]:
                text = await resp.text()
                raise Exception(f"Failed to create bucket {bucket['name']}: {resp.status} - {self._sanitize_error(text)}")
            print(f"Ensured bucket exists: {bucket['name']}")

    async def recursive_list_files(self, bucket_name: str, path: str = ""):
        limit = 100
        offset = 0
        
        while True:
            payload = {
                "prefix": path,
                "limit": limit,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"}
            }
            resp = await self._request_with_retry(
                "POST", 
                f"{self.url}/storage/v1/object/list/{bucket_name}", 
                headers=self.headers, 
                json=payload
            )
            async with resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"Error listing {bucket_name}/{path}: {self._sanitize_error(error_text)}")
                    break
                
                items = await resp.json()
                if not items:
                    break
                    
                for item in items:
                    if item.get('id') is None:
                        # Directory - recurse
                        new_path = f"{path}/{item['name']}" if path else item['name']
                        async for sub_item in self.recursive_list_files(bucket_name, new_path):
                            yield sub_item
                    else:
                        # File - yield
                        item['full_path'] = f"{path}/{item['name']}" if path else item['name']
                        yield item
                
                if len(items) < limit:
                    break
                offset += limit

    async def backup_bucket(self, bucket_name: str, target_dir: str, concurrency: int = 10):
        print(f"Backing up bucket '{bucket_name}'...")
        
        os.makedirs(f"{target_dir}/{bucket_name}", exist_ok=True)
        sem = asyncio.Semaphore(concurrency)
        
        async def _download(file_item):
            async with sem:
                full_path = file_item['full_path']
                local_path = f"{target_dir}/{bucket_name}/{full_path}"
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                with open(f"{local_path}.__metadata.json", 'w') as f:
                    json.dump(file_item, f, indent=2)

                encoded_path = quote(full_path)
                resp = await self._request_with_retry(
                    "GET", 
                    f"{self.url}/storage/v1/object/{bucket_name}/{encoded_path}", 
                    headers=self.headers
                )
                async with resp:
                    if resp.status == 200:
                        with open(local_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(1024*1024):
                                f.write(chunk)
                    else:
                        error_text = await resp.text() if resp.status != 200 else ""
                        print(f"Failed to download {full_path}: {resp.status} - {self._sanitize_error(error_text)}")

        # Create tasks for all files. 
        # While a bounded queue is better for massive scales, spawning tasks 
        # is sufficient for typical bucket sizes (<100k objects) and preserves parallelism.
        tasks = []
        
        async for file_item in self.recursive_list_files(bucket_name):
             tasks.append(asyncio.create_task(_download(file_item)))
        
        if not tasks:
             return

        for f in tqdm.tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"Backing up {bucket_name}", 
                           disable=not _should_show_progress(), mininterval=5.0):
            await f
        
        print(f"Done backing up '{bucket_name}'")

    async def restore_bucket(self, bucket_name: str, source_dir: str, concurrency: int = 10):
        bucket_source = os.path.join(source_dir, bucket_name)
        if not os.path.exists(bucket_source):
            print(f"Source directory {bucket_source} not found")
            return

        files_to_upload = []
        for root, _, filenames in os.walk(bucket_source):
            for filename in filenames:
                if filename.endswith('.__metadata.json'):
                    continue
                local_path = os.path.join(root, filename)
                rel_path = os.path.relpath(local_path, bucket_source)
                
                meta_path = f"{local_path}.__metadata.json"
                metadata = {}
                if os.path.exists(meta_path):
                    with open(meta_path, 'r') as f:
                        metadata = json.load(f)
                
                files_to_upload.append((rel_path, local_path, metadata))

        if not files_to_upload:
            return

        print(f"Restoring bucket '{bucket_name}'...")
        sem = asyncio.Semaphore(concurrency)
        
        async def _upload(item):
            rel_path, local_path, meta = item
            async with sem:
                headers = self.headers.copy()
                headers['x-upsert'] = 'true'
                
                file_meta = meta.get('metadata', {}) or {}
                mime = file_meta.get('mimetype') or 'application/octet-stream'
                
                if mime.lower() == 'image/jpg':
                    mime = 'image/jpeg'
                    
                cache = file_meta.get('cacheControl') or '3600'
                headers['Content-Type'] = mime
                headers['cache-control'] = f"max-age={cache}"
                
                encoded_path = quote(rel_path)
                with open(local_path, 'rb') as f:
                    file_content = f.read()
                
                resp = await self._request_with_retry(
                    "POST", 
                    f"{self.url}/storage/v1/object/{bucket_name}/{encoded_path}", 
                    headers=headers,
                    data=file_content
                )
                async with resp:
                    if resp.status not in [200, 201]:
                        text = await resp.text()
                        print(f"Failed to upload {rel_path}: {resp.status} - {self._sanitize_error(text)}")

        tasks = [_upload(item) for item in files_to_upload]
        for f in tqdm.tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"Restoring {bucket_name}", 
                           disable=not _should_show_progress(), mininterval=5.0):
            await f
        
        print(f"Done restoring '{bucket_name}'")

    async def delete_file(self, bucket_name: str, path: str):
        encoded_path = quote(path)
        resp = await self._request_with_retry(
            "DELETE", 
            f"{self.url}/storage/v1/object/{bucket_name}/{encoded_path}", 
            headers=self.headers
        )
        async with resp:
            if resp.status != 200:
                error_text = await resp.text() if resp.status != 200 else ""
                print(f"Failed to delete {path}: {resp.status} - {self._sanitize_error(error_text)}")

    async def wipe_bucket(self, bucket_name: str, source_dir: str, concurrency: int = 10):
        """
        Deletes files in the remote bucket that are NOT present in the local source_dir.
        """
        print(f"Cleaning bucket '{bucket_name}'...")
        
        # 1. Collect all remote files to compare against local files.
        # We need the full list to determine deletions.
        remote_files = []
        async for f in self.recursive_list_files(bucket_name):
             remote_files.append(f)

        if not remote_files:
             return

        # 2. List local files in this bucket
        bucket_source = os.path.join(source_dir, bucket_name)
        local_files_set = set()
        
        if os.path.exists(bucket_source):
            for root, _, filenames in os.walk(bucket_source):
                for filename in filenames:
                    if filename.endswith('.__metadata.json'):
                        continue
                    local_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(local_path, bucket_source)
                    # Normalize path separators just in case
                    rel_path = rel_path.replace("\\", "/")
                    local_files_set.add(rel_path)
        
        # 3. Identify files to delete
        files_to_delete = []
        for rf in remote_files:
            # rf['full_path'] is effectively the relative path from bucket root
            if rf['full_path'] not in local_files_set:
                files_to_delete.append(rf['full_path'])
        
        if not files_to_delete:
            print(f"Done cleaning '{bucket_name}'")
            return
        
        sem = asyncio.Semaphore(concurrency)
        async def _delete(path):
            async with sem:
                await self.delete_file(bucket_name, path)
        
        tasks = [_delete(p) for p in files_to_delete]
        for f in tqdm.tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"Wiping {bucket_name}", 
                           disable=not _should_show_progress(), mininterval=5.0):
            await f
        
        print(f"Done cleaning '{bucket_name}'")

async def backup(concurrency: int):
    project_ref = get_env_var("SUPABASE_PROJECT_REF")
    service_role_key = get_env_var("SUPABASE_SERVICE_ROLE_KEY")
    url = f"https://{project_ref}.supabase.co"
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    target_dir = os.path.join(local_backup_dir, "storage")
    os.makedirs(target_dir, exist_ok=True)

    async with StorageMigrator(url, service_role_key) as migrator:
        buckets = await migrator.list_buckets()
        print(f"Found {len(buckets)} buckets.")
        for bucket in buckets:
            await migrator.backup_bucket(bucket['name'], target_dir, concurrency)

async def restore(concurrency: int):
    project_ref = get_env_var("TARGET_PROJECT_REF")
    service_role_key = get_env_var("TARGET_SERVICE_ROLE_KEY")
    url = f"https://{project_ref}.supabase.co"
    
    local_backup_dir = os.path.expanduser(get_env_var("LOCAL_BACKUP_DIR", required=False) or "./backups")
    source_dir = os.path.join(local_backup_dir, "storage")

    if not os.path.isdir(source_dir):
        print(f"Error: Source directory {source_dir} does not exist.")
        sys.exit(1)

    async with StorageMigrator(url, service_role_key) as migrator:
        # Restore logic: 
        # 1. Iterate over LOCAL buckets to ensure they exist and restore them.
        print("Fetching bucket list...")
        
        remote_buckets = await migrator.list_buckets()
        remote_bucket_names = set(b['name'] for b in remote_buckets)
        
        local_bucket_names = set()
        if os.path.exists(source_dir):
             local_bucket_names = set(item for item in os.listdir(source_dir) if os.path.isdir(os.path.join(source_dir, item)))

        # A. WIPE/RESTORE buckets that exist locally
        for bucket_name in local_bucket_names:
            print(f"Processing bucket: {bucket_name}")
            await migrator.create_bucket_if_missing({'name': bucket_name})
            
            # Wipe extra files first
            await migrator.wipe_bucket(bucket_name, source_dir, concurrency)
            
            # Then restore/upload
            await migrator.restore_bucket(bucket_name, source_dir, concurrency)
        
        # B. For buckets not in backup, simple empty them
        for bucket_name in remote_bucket_names:
            if bucket_name not in local_bucket_names:
                print(f"Bucket {bucket_name} is not in backup. Wiping content...")
                # We treat it as if local dir is empty for this bucket
                await migrator.wipe_bucket(bucket_name, source_dir, concurrency) 

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Supabase Storage Backup/Restore (API based)")
    parser.add_argument("action", choices=["backup", "restore"], help="Action to perform")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="Number of concurrent transfers (default: 10)")
    args = parser.parse_args()

    if args.action == "backup":
        asyncio.run(backup(args.concurrency))
    elif args.action == "restore":
        asyncio.run(restore(args.concurrency))
