import glob
import sys
import re

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    log_files = glob.glob('logs/*.log*')
    # Filter out llm_decisions.log and verdict_audit.log
    log_files = [f for f in log_files if "llm_decisions" not in f and "verdict_audit" not in f]
    print("Searching log files:", log_files)
    
    pattern = re.compile(r"(ПЕРЕХОПЛЕНО|intercepted|review/history)", re.IGNORECASE)
    
    found = False
    for filename in log_files:
        try:
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if pattern.search(line):
                        print(f"{filename}: {line.strip()}")
                        found = True
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            
    if not found:
        print("No intercepts found in system logs.")

if __name__ == '__main__':
    main()
