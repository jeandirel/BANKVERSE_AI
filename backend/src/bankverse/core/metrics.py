"""Prometheus metrics (scraped by CloudWatch agent / Amazon Managed Prometheus in AWS)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "bankverse_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "bankverse_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
MODEL_INFERENCES = Counter(
    "bankverse_model_inferences_total", "Model inferences served", ["model", "version", "status"]
)
MODEL_LATENCY = Histogram(
    "bankverse_model_inference_seconds", "Model inference latency", ["model"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
ALERTS_CREATED = Counter("bankverse_alerts_created_total", "Alerts created", ["module", "band"])
PIPELINE_RUNS = Counter("bankverse_pipeline_runs_total", "Pipeline runs", ["pipeline", "status"])
AGENT_RUNS = Counter("bankverse_agent_runs_total", "Agent runs", ["agent", "status"])
LLM_CALLS = Counter("bankverse_llm_calls_total", "LLM provider calls", ["provider", "status"])
DRIFT_PSI = Gauge("bankverse_model_drift_psi", "Max feature PSI in last drift check", ["model"])
