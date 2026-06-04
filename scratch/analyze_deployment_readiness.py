# scratch/analyze_deployment_readiness.py
import os
import re
import sys
import subprocess
from pathlib import Path

# Set stdout to UTF-8 to prevent encoding errors on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Base list of standard library modules to filter out
stdlib = {
    "os", "sys", "re", "time", "json", "asyncio", "logging", "hashlib", "uuid", 
    "pathlib", "typing", "contextlib", "datetime", "sqlite3", "math", "collections", 
    "functools", "abc", "traceback", "random", "string", "copy", "importlib", "inspect",
    "shutil", "tempfile", "urllib", "enum", "numbers", "decimal", "struct", "base64"
}

def scan_imports(root_dir: Path):
    imports = set()
    for root, dirs, files in os.walk(root_dir):
        # Skip virtual environments, backups, scratch, and git
        if any(p in Path(root).parts for p in (".venv", ".git", "scratch", "scratch_out", "_backup_v1_20260312_234529")):
            continue
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # Search "import name"
                    for match in re.finditer(r"^\s*import\s+([a-zA-Z0-9_\.,\s]+)", content, re.MULTILINE):
                        parts = match.group(1).split(",")
                        for part in parts:
                            part = part.strip().split(".")[0].split(" ")[0].strip()
                            if part:
                                imports.add(part)
                                
                    # Search "from name import ..."
                    for match in re.finditer(r"^\s*from\s+([a-zA-Z0-9_\.]+)\s+import", content, re.MULTILINE):
                        part = match.group(1).split(".")[0].strip()
                        if part:
                            imports.add(part)
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")
                    
    # Exclude standard library and local modules
    project_modules = {"api", "bot", "config", "core", "exchanges", "filters", "infrastructure", "scanner", "state", "tools"}
    third_party = imports - stdlib - project_modules
    return sorted(list(third_party))

def check_requirements():
    req_file = Path("requirements.txt")
    if not req_file.exists():
        return []
    
    with open(req_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    reqs = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            reqs.append(line.split(">")[0].split("=")[0].split("<")[0].strip())
    return reqs

def run_tests():
    print("=== Running pytest ===")
    try:
        # We need to run pytest with utf-8 encoding set, or run it through python
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        res = subprocess.run([sys.executable, "-m", "pytest"], capture_output=True, text=True, timeout=30, env=env)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)

if __name__ == "__main__":
    root = Path(".")
    tp_imports = scan_imports(root)
    reqs = check_requirements()
    
    # Normalize reqs to lower case for comparison
    reqs_lower = {r.lower() for r in reqs}
    
    # Map imported name to potential PyPI package names if they differ
    import_to_pypi = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "aiosqlite": "aiosqlite",
        "aiohttp": "aiohttp",
        "prometheus_client": "prometheus-client",
        "pydantic_settings": "pydantic-settings",
        "dotenv": "python-dotenv",
        "cryptography": "cryptography",
        "aiogram": "aiogram",
        "curl_cffi": "curl-cffi",
        "tenacity": "tenacity",
    }
    
    print("Detected Third-Party Imports in codebase:")
    for imp in tp_imports:
        pypi_name = import_to_pypi.get(imp, imp)
        status = "Present in requirements.txt" if pypi_name.lower() in reqs_lower else "MISSING from requirements.txt"
        print(f"  - {imp} (PyPI package: {pypi_name}): {status}")
        
    print("\nRequirements specified in requirements.txt:")
    print("  - " + ", ".join(reqs))
    
    dockerfile_size = Path("Dockerfile").stat().st_size if Path("Dockerfile").exists() else 0
    compose_size = Path("docker-compose.yml").stat().st_size if Path("docker-compose.yml").exists() else 0
    
    print(f"\nDocker configuration files:")
    print(f"  - Dockerfile: {'Exists but is EMPTY' if dockerfile_size == 0 else f'Size: {dockerfile_size} bytes'}")
    print(f"  - docker-compose.yml: {'Exists but is EMPTY' if compose_size == 0 else f'Size: {compose_size} bytes'}")
    
    # Run tests
    code, out, err = run_tests()
    print(f"\npytest exit code: {code}")
    if code != 0:
        print("Tests failed or errored. Output excerpt:")
        lines = out.splitlines() + err.splitlines()
        for line in lines[:50]:
            print(f"  {line}")
    else:
        print("All tests passed successfully!")
