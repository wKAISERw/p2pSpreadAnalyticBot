import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    print("--- Reading logs/debug.log head ---")
    try:
        with open('logs/debug.log', 'r', encoding='utf-8', errors='replace') as f:
            for i in range(20):
                line = f.readline()
                if not line:
                    break
                print(line.strip())
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    main()
