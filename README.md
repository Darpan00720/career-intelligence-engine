🚀 Career Intelligence Engine

An AI-powered, production-ready career intelligence platform that transforms job discovery into a data-driven decision system. The engine analyzes candidate profiles and opportunities, applies semantic intelligence and graph-based reasoning, and generates personalized, explainable career recommendations.

⸻

🎯 Vision

Career Intelligence Engine aims to become an AI career co-pilot that not only finds jobs but also understands candidate strengths, evaluates opportunities, prioritizes actions, and delivers actionable career intelligence through explainable AI workflows.

⸻

✨ Features

🤖 AI-Powered Career Intelligence

* Semantic job matching using embeddings
* Explainable recommendation engine
* Opportunity prioritization and ranking
* Eligibility scoring framework
* Candidate profile intelligence

🧠 LangGraph Workflows

* Stateful graph orchestration
* Multi-step reasoning workflows
* Persistent checkpoints
* Deterministic execution

🏢 Company Intelligence

* Company tier classification
* Opportunity intelligence generation
* Company watchlists
* Eligibility analysis

🌐 Production APIs

* Health / readiness / version endpoints
* Career intelligence + CRM REST API (jobs, recommendations, analytics, applications)
* Versioned Gateway (v2) for workflows, experiments, metrics, tenants, usage

🔐 Security & Multi-Tenancy

* API-key authentication (enforced when `API_KEYS` is configured)
* Tenant bound to the API key — closes cross-tenant access on the v2 surface
* Per-tenant fixed-window rate limiting
* PII masking, retention, and GDPR tenant deletion helpers

📊 Observability

* Structured logging
* Health monitoring
* Readiness checks
* System diagnostics

⸻

🏗️ System Architecture

Candidate Profile
↓
Semantic Matching Engine
↓
Eligibility & Scoring Engine
↓
LangGraph Workflow Orchestrator
↓
Recommendation Engine
↓
Career Intelligence API
↓
Actionable Career Insights

⸻

🛠️ Technology Stack

Layer	Technologies
Backend	Python, FastAPI
AI Orchestration	LangGraph
Semantic Search	Sentence Transformers
Database	SQLite
Containerization	Docker, Docker Compose
Testing	Pytest
Version Control	Git, GitHub

⸻

📂 Project Structure

agents/        AI agents and workflows
api/           FastAPI application
core/          Business logic and intelligence engine
graph/         LangGraph workflows
schemas/       Data schemas
services/      Application services
tests/         Automated tests
data/          Runtime data and dictionaries
docs/          Architecture and specifications
prompts/       AI prompts

⸻

🚀 Quick Start

Clone Repository

git clone https://github.com/Darpan00720/career-intelligence-engine.git
cd career-intelligence-engine

Install Dependencies

pip install -r requirements.txt

Run with Docker

docker compose up --build

Verify Service

curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/version

⸻

🔐 Authentication

The ops endpoints (`/health`, `/ready`, `/version`) are always open. The CRM
(`/api/*`) and Gateway (`/api/v2/*`) surfaces require an API key **when configured**:

```
# .env — comma-separated keys, each optionally bound to a tenant
API_KEYS=key_acme:acme,key_globex:globex
```

When `API_KEYS` is set, send `X-API-Key: key_acme`; the tenant is derived from the
key (a client cannot read another tenant's data). With no keys configured the API
runs in open/dev mode. Production deployments should always set `API_KEYS`.

⸻

📡 API Endpoints

Health

GET /health

Response:

{
  "status": "healthy"
}

Readiness

GET /ready

Response:

{
  "status": "ready",
  "database": true,
  "checkpoint_storage": true,
  "graph_compiled": true,
  "embeddings_loaded": true
}

Version

GET /version

Response:

{
  "name": "Career Intelligence Engine",
  "version": "7.1.2",
  "system_certified": true
}

⸻

🧪 Testing

pytest

⸻

📦 Release Information

Current Version: v7.1.2

Release Status: Production Ready ✅

Production Validation

* ✅ System Certified
* ✅ Docker Deployment Operational
* ✅ Health Endpoint Healthy
* ✅ Readiness Endpoint Ready
* ✅ Database Loaded
* ✅ Embeddings Loaded
* ✅ Graph Compiled
* ✅ Checkpoint Storage Initialized
* ✅ Production Tag Created

⸻

👨‍💻 Author

Darpan Jain

MBA Candidate | AI Product Management | Digital Transformation | Technology Strategy

⸻

📄 License

This project is released for educational, research, and portfolio purpose

