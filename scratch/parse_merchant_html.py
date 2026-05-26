import re
import sys

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    with open("scratch/okx_merchant.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    print("--- Searching for review / feedback keywords in raw HTML ---")
    keywords = ["отзывы", "відгуки", "review", "feedback", "положительные", "отрицательные", "history"]
    
    for kw in keywords:
        matches = [m.start() for m in re.finditer(kw, html, re.IGNORECASE)]
        print(f"Keyword '{kw}': {len(matches)} matches")
        for idx in matches[:5]:
            # Print a snippet around the match
            start = max(0, idx - 50)
            end = min(len(html), idx + 150)
            snippet = html[start:end].replace('\n', ' ')
            print(f"  Match: ... {snippet} ...")

if __name__ == '__main__':
    main()
