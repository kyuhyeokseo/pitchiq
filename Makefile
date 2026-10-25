PY ?= .venv/bin/python
export PYTHONPATH := backend
# optional prefix for walkthrough and other heavy targets, e.g. on a cluster: SRUN="srun -p <partition> -n 1 -c 4"
SRUN ?=

.PHONY: setup install test fixtures fixtures-v1 dev build walkthrough evaluate foundry-smoke validate sanity plot eval motion

setup install:      ## venv + pinned deps (editable)
	uv venv .venv --python 3.12
	uv pip install --python $(PY) -c infra/constraints.txt -e ".[dev,foundry,mcp,telemetry]"

test:               ## full backend test suite (v2 defensive intelligence + v1 overlap prototype)
	$(PY) -m pytest

fixtures:           ## regenerate the committed v2 public clips + private truth (deterministic)
	$(PY) -m app.simulator.defense.generate --fixtures

build:              ## typecheck + build the React frontend into frontend/dist
	scripts/build_frontend.sh

dev:                ## API + built frontend on :8000 (on shared HPC run it inside srun, not on a login node)
	$(PY) -m uvicorn app.api.app:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1

walkthrough:        ## real headless-browser walkthrough (writes docs/walkthrough/), via $(SRUN) on a compute node
	$(SRUN) $(PY) evaluation/walkthrough/walkthrough.py --out docs/walkthrough

evaluate:           ## v2 holdout evaluation (evaluation/holdout_v2.yaml -> evaluation/reports/holdout_v2/<version>/)
	$(PY) evaluation/holdout_v2.py

foundry-smoke:      ## one real Foundry Analyst -> Producer run (needs .env, the foundry extra and az login)
	$(PY) -m app.agents.smoke

# ---- v1 overlap prototype (P1 in spec v2) ----
fixtures-v1:
	$(PY) -m app.simulator.generate --demo
validate:
	$(PY) -m app.simulator.validate data/fixtures/scenario_a --truth data/private_truth/scenario_a.jsonl
sanity:
	$(PY) evaluation/scripts/generator_sanity.py --seeds 60
plot:
	$(PY) -m app.simulator.plot data/fixtures/scenario_a c02 --out evaluation/reports/c02.png
eval:
	$(PY) -m app.simulator.generate --seed 4242 --clips 200 --output data/generated/eval_s4242
	$(PY) -m app.simulator.generate --seed 9001 --clips 200 --output data/generated/test_s9001
	$(PY) evaluation/scripts/eval_rules.py data/generated/eval_s4242 --out evaluation/reports/rules_eval_dev_s4242.json
	$(PY) evaluation/scripts/eval_rules.py data/generated/test_s9001 --out evaluation/reports/rules_eval_test_s9001.json
motion:
	$(PY) evaluation/scripts/motion_realism.py data/generated/test_s9001 --out evaluation/reports/motion_realism_test_s9001.json
