from core.analysis.rules import ALL_RULES

REVIEW_ONLY_RULES = [r for r in ALL_RULES if getattr(r, "review_only", False)]

def analyze_review_text(text: str) -> dict:
    if not text:
        return {"text": "", "score": 0, "categories": [], "excerpts":[]}

    from core.analysis.regex_analyzer import _clean_text, _fuzzy_text, _excerpt
    raw = _clean_text(text)
    fuzzy = _fuzzy_text(raw)

    score = 0
    categories = []
    excerpts =[]

    for rule in REVIEW_ONLY_RULES:
        m = rule.pattern.search(raw) or rule.pattern.search(fuzzy)
        if m:
            score += rule.weight
            if rule.category not in categories:
                categories.append(rule.category)
            excerpts.append(f"{rule.category}: {_excerpt(raw, m)}")

    return {
        "text": text[:200],
        "score": min(100, max(0, score)),
        "categories": categories,
        "excerpts": excerpts[:3],
    }