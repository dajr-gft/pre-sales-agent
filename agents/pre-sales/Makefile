# ==============================================================================
# Environment Variables & Detection
# ==============================================================================
ifeq ($(OS),Windows_NT)
    VENV_BIN := .venv/Scripts
else
    VENV_BIN := .venv/bin
endif

# ==============================================================================
# Installation & Setup
# ==============================================================================

# Install dependencies using pip
install:
	@command -v python >/dev/null 2>&1 || { echo "Python is not installed."; exit 1; }
	@if [ ! -d ".venv" ]; then python -m venv .venv; fi
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -r requirements.txt

# ==============================================================================
# Playground Targets
# ==============================================================================

# Launch local dev playground
playground:
	@echo "==============================================================================="
	@echo "| 🚀 Starting your agent playground...                                        |"
	@echo "|                                                                             |"
	@echo "| 💡 Try asking: What's the weather in San Francisco?                         |"
	@echo "|                                                                             |"
	@echo "| 🔍 IMPORTANT: Select the 'app' folder to interact with your agent.          |"
	@echo "==============================================================================="
	$(VENV_BIN)/adk web . --port 8501 --reload_agents

# ==============================================================================
# Backend Deployment Targets
# ==============================================================================

# Deploy the agent remotely
deploy:
	# Copy production requirements (without dev deps) for deployment
	cp requirements.txt app/app_utils/.requirements.txt && \
	$(VENV_BIN)/python -m app.app_utils.deploy \
		--source-packages=./app \
		--source-packages="./startup_scripts" \
		--entrypoint-module=app.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=app/app_utils/.requirements.txt \
		$(if $(AGENT_IDENTITY),--agent-identity) \
		$(if $(filter command line,$(origin SECRETS)),--set-secrets="$(SECRETS)")

backend: deploy

# ==============================================================================
# Infrastructure Setup
# ==============================================================================

setup-dev-env:
	PROJECT_ID=$$(gcloud config get-value project) && \
	(cd deployment/terraform/dev && terraform init && terraform apply --var-file vars/env.tfvars --var dev_project_id=$$PROJECT_ID --auto-approve)

# ==============================================================================
# Testing & Code Quality
# ==============================================================================

test:
	$(VENV_BIN)/pip install -r requirements-dev.txt
	$(VENV_BIN)/pytest tests/unit && $(VENV_BIN)/pytest tests/integration

eval:
	@echo "==============================================================================="
	@echo "| Running Agent Evaluation                                                    |"
	@echo "==============================================================================="
	$(VENV_BIN)/pip install -r requirements-dev.txt "google-adk[eval]==1.27.1"
	$(VENV_BIN)/adk eval ./app $${EVALSET:-tests/eval/evalsets/basic.evalset.json} \
		$(if $(EVAL_CONFIG),--config_file_path=$(EVAL_CONFIG),$(if $(wildcard tests/eval/eval_config.json),--config_file_path=tests/eval/eval_config.json,))

eval-all:
	@echo "==============================================================================="
	@echo "| Running All Evalsets                                                        |"
	@echo "==============================================================================="
	@for evalset in tests/eval/evalsets/*.evalset.json; do \
		echo ""; \
		echo "▶ Running: $$evalset"; \
		$(MAKE) eval EVALSET=$$evalset || exit 1; \
	done
	@echo ""
	@echo "✅ All evalsets completed"

lint:
	$(VENV_BIN)/pip install -r requirements-dev.txt "ruff>=0.4.6,<1.0.0" "ty>=0.0.1a0" "codespell>=2.2.0,<3.0.0"
	$(VENV_BIN)/codespell
	$(VENV_BIN)/ruff check . --diff
	$(VENV_BIN)/ruff format . --check --diff
	$(VENV_BIN)/ty check .

# ==============================================================================
# Gemini Enterprise Integration
# ==============================================================================

register-gemini-enterprise:
	@pipx run agent-starter-pack==0.41.0 register-gemini-enterprise