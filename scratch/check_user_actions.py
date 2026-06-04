import sys

sys.stdout.reconfigure(encoding='utf-8')

def main():
    user_id = "1115620363"
    with open("logs/debug.log", "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    print(f"Total lines: {len(lines)}")
    matches = []
    for i, line in enumerate(lines):
        if user_id in line:
            matches.append((i, line.strip()))
            
    print(f"Total matches for user {user_id}: {len(matches)}")
    for i, line in matches[-30:]:
        print(f"Line {i}: {line}")

if __name__ == "__main__":
    main()
