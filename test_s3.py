import os
import boto3
from dotenv import load_dotenv
import uuid

def test_s3_connection():
    load_dotenv()
    
    print("Testing S3 connection (TEST credentials)...")
    
    access_key = os.getenv("TEST_S3_ACCESS_KEY_ID")
    secret_key = os.getenv("TEST_S3_SECRET_ACCESS_KEY")
    endpoint = os.getenv("TEST_S3_ENDPOINT")
    region = os.getenv("TEST_S3_REGION", "us-east-1")
    
    if not all([access_key, secret_key, endpoint]):
        print("Error: Missing required TEST_S3_* environment variables.")
        return False
        
    try:
        session = boto3.session.Session()
        s3 = session.client(
            service_name='s3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint,
            region_name=region
        )
        
        # 1. List buckets
        print("Listing buckets...")
        response = s3.list_buckets()
        buckets = [b['Name'] for b in response.get('Buckets', [])]
        print(f"Buckets found: {buckets}")
        
        print("S3 connection test PASSED (Bucket operations skipped usually require a specific bucket).")
        return True
        
    except Exception as e:
        print(f"S3 connection test FAILED: {e}")
        return False

if __name__ == "__main__":
    test_s3_connection()
