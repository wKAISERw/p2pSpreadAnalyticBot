# core/analytics/metrics.py
"""
Prometheus metrics for Arbix Quantum.

Tracks:
  - LLM request counts and latency by provider
  - RiskEngine cache hits/misses
  - Spread alerts sent/redrawn
  - Review fetch counts and latency
  - Feedback loop (manual blacklist) actions
"""
from prometheus_client import Counter, Histogram, Gauge

# ── LLM metrics ────────────────────────────────────────────────────────────────
llm_requests_total = Counter(
    "llm_requests_total",
    "Total number of LLM requests by provider and status",
    ["provider", "status"]
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM request latency in seconds by provider",
    ["provider"],
    buckets=(0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 20.0, 30.0),
)

# ── RiskEngine cache metrics ───────────────────────────────────────────────────
cache_requests_total = Counter(
    "risk_engine_cache_requests_total",
    "Total cache requests to RiskEngine caches",
    ["cache_type", "result"]
)

# ── Alert metrics ──────────────────────────────────────────────────────────────
alerts_sent_total = Counter(
    "alerts_sent_total",
    "Total Telegram spread alerts sent to users",
    ["route_type"]
)

alerts_redrawn_total = Counter(
    "alerts_redrawn_total",
    "Total Telegram alert messages redrawn (edited) after LLM verdict update",
    ["exchange"]
)

alerts_blocked_total = Counter(
    "alerts_blocked_total",
    "Total alerts skipped due to BLOCK risk flag",
    ["exchange"]
)

# ── Review fetch metrics ───────────────────────────────────────────────────────
review_fetches_total = Counter(
    "review_fetches_total",
    "Total review fetch attempts by exchange and status",
    ["exchange", "status"]
)

review_fetch_duration_seconds = Histogram(
    "review_fetch_duration_seconds",
    "Time taken to fetch reviews per exchange",
    ["exchange"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# ── Spread metrics ─────────────────────────────────────────────────────────────
spreads_found_total = Counter(
    "spreads_found_total",
    "Total spread opportunities found per cycle (before filters)",
    ["route_type"]
)

spread_pct_histogram = Histogram(
    "spread_pct",
    "Distribution of spread percentages found",
    buckets=(0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0),
)

# ── Feedback loop metrics ──────────────────────────────────────────────────────
feedback_blacklist_total = Counter(
    "feedback_blacklist_total",
    "Total manual blacklist actions from Telegram feedback buttons",
    ["action"]
)

# ── System health ──────────────────────────────────────────────────────────────
scanner_cycle_duration_seconds = Histogram(
    "scanner_cycle_duration_seconds",
    "Duration of each scanner main loop cycle",
    buckets=(0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0),
)

llm_queue_size = Gauge(
    "llm_queue_size",
    "Current number of tasks pending in the LLM worker queue",
)

review_queue_size = Gauge(
    "review_queue_size",
    "Current number of merchants pending in the ReviewFetcher queue",
)
