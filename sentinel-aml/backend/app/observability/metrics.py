"""Prometheus metrics (scraped at /metrics; Azure Monitor managed Prometheus in production)."""
from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["method", "route"],
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
ML_PREDICTIONS = Counter("ml_predictions_total", "Model predictions", ["model_version"])
ML_SCORE = Histogram("ml_risk_score", "Calibrated risk score distribution", buckets=[i / 10 for i in range(11)])
ML_LATENCY = Histogram("ml_inference_seconds", "Model inference latency", buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5))
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["model", "prompt", "status"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "direction"])
LLM_COST = Counter("llm_cost_usd_total", "Estimated LLM cost (USD)", ["model"])
LLM_LATENCY = Histogram("llm_latency_seconds", "LLM latency", ["model"], buckets=(0.05, 0.25, 0.5, 1, 2, 5, 10, 20, 45))
AGENT_STEP_LATENCY = Histogram("agent_step_seconds", "Agent step latency", ["agent", "status"],
                               buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60))
TOOL_CALLS = Counter("agent_tool_calls_total", "Agent tool calls", ["agent", "tool", "status"])
RAG_LATENCY = Histogram("rag_retrieval_seconds", "RAG retrieval latency", buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1))
RAG_RESULTS = Histogram("rag_results_returned", "Chunks returned after ACL filtering", buckets=(0, 1, 2, 4, 6, 8, 12))
WORKFLOWS = Counter("workflows_total", "Investigation workflows by terminal status", ["status"])
INJECTION_FLAGS = Counter("prompt_injection_flags_total", "Prompt-injection detections", ["source"])
