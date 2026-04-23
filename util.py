import os
import re
import shutil
import signal
import subprocess
import sys

def check_tool(tool_name, error_message=None, path=None):
    """Checks if a tool is available in the system or specified path."""
    if not shutil.which(tool_name, path=path):
        if error_message:
            print(error_message)
        else:
             print(f"Error: '{tool_name}' CLI not found.")
        sys.exit(1)

def _resolve_timeout(timeout_seconds=None):
    if timeout_seconds is not None:
        return timeout_seconds if timeout_seconds > 0 else None

    raw_timeout = os.getenv("COMMAND_TIMEOUT_SEC", "").strip()
    if not raw_timeout:
        return None

    try:
        parsed = int(raw_timeout)
        return parsed if parsed > 0 else None
    except ValueError:
        print(f"Warning: Ignoring invalid COMMAND_TIMEOUT_SEC={raw_timeout!r}")
        return None


def run_command(command, env=None, capture=False, timeout_seconds=None, kill_grace_seconds=15):
    """Executes a shell command with optional timeout.

    Timeout comes from explicit arg or COMMAND_TIMEOUT_SEC env var.
    On timeout, whole process group is terminated.
    """
    # Censor password in printed command: postgresql://user:password@host -> postgresql://user:*****@host
    censored_command = re.sub(r'(://[^:]+):([^@]+)@', r'\1:*****@', command)
    print(f"Executing: {censored_command}")

    resolved_timeout = _resolve_timeout(timeout_seconds)
    popen_kwargs = {
        "shell": True,
        "env": env,
        "text": True,
        "start_new_session": True,
    }

    if capture:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE

    process = subprocess.Popen(command, **popen_kwargs)

    try:
        stdout, stderr = process.communicate(timeout=resolved_timeout)
    except subprocess.TimeoutExpired:
        print(f"Error: Command timed out after {resolved_timeout} seconds")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            stdout, stderr = process.communicate(timeout=kill_grace_seconds)
        except subprocess.TimeoutExpired:
            print("Error: Command ignored SIGTERM. Sending SIGKILL.")
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()

        if capture:
            print(f"Stdout: {stdout}")
            print(f"Stderr: {stderr}")
        return False

    if process.returncode != 0:
        print(f"Error: Command failed with return code {process.returncode}")
        if capture:
            print(f"Stdout: {stdout}")
            print(f"Stderr: {stderr}")
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
