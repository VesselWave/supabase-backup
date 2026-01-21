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
    print(f"Executing: {command}")
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
