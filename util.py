import os
import subprocess
import sys
import shutil

def check_tool(tool_name, error_message=None, path=None):
    """Checks if a tool is available in the system or specified path."""
    if not shutil.which(tool_name, path=path):
        if error_message:
            print(error_message)
        else:
             print(f"Error: '{tool_name}' CLI not found.")
        sys.exit(1)

def run_command(command, env=None, capture=False):
    """Executes a shell command and exits on failure."""
    import re
    # Censor password in printed command: postgresql://user:password@host -> postgresql://user:*****@host
    censored_command = re.sub(r'(://[^:]+):([^@]+)@', r'\1:*****@', command)
    print(f"Executing: {censored_command}")
    
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

def get_db_url(prefix="", required=True):
    """Constructs the database URL from SUPABASE_PROJECT_REF and SUPABASE_DB_PASSWORD."""
    # Derived DB URL if explicitly provided
    explicit_url = os.getenv(f"{prefix}SUPABASE_DB_URL")
    if explicit_url:
        return os.path.expandvars(explicit_url)
    
    ref = os.getenv(f"{prefix}SUPABASE_PROJECT_REF")
    password = os.getenv(f"{prefix}SUPABASE_DB_PASSWORD")
    
    if not ref or not password:
        if required:
            print(f"Error: {prefix}SUPABASE_PROJECT_REF and {prefix}SUPABASE_DB_PASSWORD must be set if {prefix}SUPABASE_DB_URL is missing.")
            sys.exit(1)
        return None
    
    return f"postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres"
