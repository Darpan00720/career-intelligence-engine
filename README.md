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

* Health endpoint
* Readiness endpoint
* Version endpoint
* Career intelligence APIs

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
  "version": "1.0.0",
  "system_certified": true
}

⸻

🧪 Testing

pytest

⸻

📦 Release Information

Current Version: v1.0.0

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

