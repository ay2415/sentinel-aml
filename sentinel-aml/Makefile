PY ?= python
export DATABASE_URL ?= postgresql+psycopg2://sentinel:sentinel@localhost:5432/sentinel
.PHONY: install embeddings pipeline api worker dashboard test test-pg lint security eval all

install:      ## backend + dev dependencies
	$(PY) -m pip install -r backend/requirements-dev.txt
embeddings:   ## download GloVe vectors and build the 100k-word cache
	$(PY) scripts/download_embeddings.py
pipeline:     ## generate -> ingest -> scan -> train -> score -> index -> demo users
	cd backend && $(PY) -m app.pipeline --reset --all
api:
	cd backend && $(PY) -m uvicorn app.main:app --reload --port 8000
worker:
	cd backend && $(PY) -m app.worker
dashboard:
	cd frontend && API_URL=http://localhost:8000 streamlit run app.py
test:         ## fast suite on SQLite
	cd backend && $(PY) -m pytest
test-pg:      ## full suite on PostgreSQL + pgvector
	cd backend && TEST_DATABASE_URL=postgresql+psycopg2://sentinel:sentinel@localhost:5432/sentinel_test $(PY) -m pytest
lint:
	cd backend && ruff check app tests
security:
	cd backend && bandit -q -r app -c pyproject.toml && pip-audit -r requirements.txt
eval:         ## measured evaluations -> evaluation/reports/*.json
	$(PY) evaluation/rag_eval.py && $(PY) evaluation/agent_eval.py && $(PY) evaluation/system_eval.py
all: lint security test pipeline eval
