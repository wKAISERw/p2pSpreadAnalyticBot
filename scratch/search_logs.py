import glob
import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    log_files = glob.glob('logs/*.log*')
    print("Searching log files:", log_files)
    
    keywords = ["ПЕРЕХОПЛЕНО", "review/history", "SessionManager"]
    
    count = 0
    for filename in log_files:
        print(f"\nSearching {filename}...")
        try:
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if any(k in line for k in keywords):
                        print(line.strip())
                        count += 1
                        if count > 200:
                            print("Reached limit of 200 matches. Stopping.")
                            return
        except Exception as e:
            print(f"Error reading {filename}: {e}")

if __name__ == '__main__':
    main()
