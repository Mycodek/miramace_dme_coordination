PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PATIENT ?= PAT-ELEANOR
HOST ?= 127.0.0.1
PORT ?= 8000
# use_llm=true → .env LLM (gemini/openai); false (default) → FakeLLM
use_llm ?= false
LLM_FLAGS := $(if $(filter true 1 yes TRUE YES,$(use_llm)),,--fake-llm)

.PHONY: help setup install clean clean-all test server demos 1 2 3 4 5 6 7 8 9 10 11 12 \
	demo-happy demo-pcp demo-supplier

help:
	@echo "Mira Mace DME Coordinator"
	@echo ""
	@echo "Setup / use"
	@echo "  make setup          fresh start: clear cases.db, .venv, .env if missing, install"
	@echo "  make install        reinstall into existing .venv"
	@echo "  make clean          clear cases.db, caches, build artifacts (keeps .venv/.env)"
	@echo "  make clean-all      clean + remove .venv (run make setup after)"
	@echo "  make test           run pytest"
	@echo "  make server         free PORT then uvicorn (HOST/PORT overridable)"
	@echo ""
	@echo "Demos (numbered)"
	@echo "  make 1              happy_path"
	@echo "  make 2              pcp_timeout"
	@echo "  make 3              supplier_failure (commitment breach)"
	@echo "  make 4              happy_path_direct"
	@echo "  make 5              happy_path_pcp_retry"
	@echo "  make 6              happy_path_confirmed_delivery"
	@echo "  make 7              happy_path_after_no_assignment"
	@echo "  make 8              pcp_incomplete_order"
	@echo "  make 9              supplier_exhausted"
	@echo "  make 10             supplier_no_assignment (patient yes → book)"
	@echo "  make 11             policy_weight_ineligible (PAT-MARCUS)"
	@echo "  make 12             supplier_no_assignment_declined (patient no)"
	@echo "  make demos          list numbered demos"
	@echo ""
	@echo "Overrides"
	@echo "  PATIENT=PAT-JAMES make 1"
	@echo "  make 1                         FakeLLM (default)"
	@echo "  make 1 use_llm=true             real .env LLM (gemini/openai)"
	@echo "  make 2 use_llm=true             same for pcp_timeout"

setup:
	@rm -f data/cases.db data/cases.db-wal data/cases.db-shm
	@echo "Cleared case data (data/cases.db)."
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example — fill GEMINI_API_KEY / OPENAI_API_KEY as needed."; \
	else \
		echo ".env already exists — left unchanged."; \
	fi
	@echo "Ready (fresh). Activate with: source .venv/bin/activate"

install:
	$(PIP) install -e ".[dev]"

clean:
	@rm -f data/cases.db data/cases.db-wal data/cases.db-shm
	@rm -rf .pytest_cache dist build *.egg-info .eggs
	@find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name '*.py[cod]' -o -name '*.egg' \) -not -path './.venv/*' -delete 2>/dev/null || true
	@pids=$$(lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "Stopping listeners on port $(PORT) (pids: $$pids)..."; \
		kill $$pids 2>/dev/null || true; \
	fi
	@echo "Clean done (kept .venv and .env)."

clean-all: clean
	@rm -rf .venv
	@echo "Removed .venv. Run: make setup"

test:
	$(PYTHON) -m pytest -q

server:
	@pids=$$(lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "Freeing port $(PORT) (pids: $$pids)..."; \
		kill $$pids 2>/dev/null || true; \
		sleep 0.3; \
		pids=$$(lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
		if [ -n "$$pids" ]; then kill -9 $$pids 2>/dev/null || true; fi; \
	fi
	$(PYTHON) -m uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

demos:
	@echo "1   happy_path                      PATIENT=$(PATIENT)"
	@echo "2   pcp_timeout                     PATIENT=$(PATIENT)"
	@echo "3   supplier_failure                PAT-JAMES"
	@echo "4   happy_path_direct               PATIENT=$(PATIENT)"
	@echo "5   happy_path_pcp_retry            PATIENT=$(PATIENT)"
	@echo "6   happy_path_confirmed_delivery   PATIENT=$(PATIENT)"
	@echo "7   happy_path_after_no_assignment  PATIENT=$(PATIENT)"
	@echo "8   pcp_incomplete_order            PATIENT=$(PATIENT)"
	@echo "9   supplier_exhausted              PATIENT=$(PATIENT)"
	@echo "10  supplier_no_assignment          PATIENT=$(PATIENT) (patient says yes → book)"
	@echo "11  policy_weight_ineligible        PAT-MARCUS"
	@echo "12  supplier_no_assignment_declined PATIENT=$(PATIENT) (patient says no → escalate)"


1:
	$(PYTHON) -m app.demo --scenario happy_path --patient $(PATIENT) $(LLM_FLAGS)

2:
	$(PYTHON) -m app.demo --scenario pcp_timeout --patient $(PATIENT) $(LLM_FLAGS)

3:
	$(PYTHON) -m app.demo --scenario supplier_failure --patient PAT-JAMES $(LLM_FLAGS)

4:
	$(PYTHON) -m app.demo --scenario happy_path_direct --patient $(PATIENT) $(LLM_FLAGS)

5:
	$(PYTHON) -m app.demo --scenario happy_path_pcp_retry --patient $(PATIENT) $(LLM_FLAGS)

6:
	$(PYTHON) -m app.demo --scenario happy_path_confirmed_delivery --patient $(PATIENT) $(LLM_FLAGS)

7:
	$(PYTHON) -m app.demo --scenario happy_path_after_no_assignment --patient $(PATIENT) $(LLM_FLAGS)

8:
	$(PYTHON) -m app.demo --scenario pcp_incomplete_order --patient $(PATIENT) $(LLM_FLAGS)

9:
	$(PYTHON) -m app.demo --scenario supplier_exhausted --patient $(PATIENT) $(LLM_FLAGS)

10:
	$(PYTHON) -m app.demo --scenario supplier_no_assignment --patient $(PATIENT) $(LLM_FLAGS)

11:
	$(PYTHON) -m app.demo --scenario policy_weight_ineligible --patient PAT-MARCUS $(LLM_FLAGS)

12:
	$(PYTHON) -m app.demo --scenario supplier_no_assignment_declined --patient $(PATIENT) $(LLM_FLAGS)
