PYTHON ?= .venv/bin/python
PORT ?= 8000

.PHONY: help venv install data mini-data facts test build start dev clean-runs

help:
	@printf '%s\n' \
	  'make venv      Create .venv with Python 3.11+' \
	  'make install   Install Python and frontend dependencies' \
	  'make data      Validate/stage the local Olist CSV files' \
	  'make mini-data Generate a 10-order Olist sample for fast testing' \
	  'make facts     Print independent data facts used by the demo' \
	  'make test      Run the backend test suite' \
	  'make build     Typecheck and build the frontend' \
	  'make start     Serve the built UI and API on one origin' \
	  'make dev       Run the API and Vite dev server together' \
	  'make clean-runs Remove local run snapshots and artifacts'

venv:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt

install:
	$(PYTHON) -m pip install -r backend/requirements.txt
	cd frontend && npm install

data:
	$(PYTHON) scripts/prepare_data.py

mini-data:
	$(PYTHON) scripts/make_mini_dataset.py --force
	$(PYTHON) scripts/prepare_data.py --dest data/olist_mini

facts:
	$(PYTHON) scripts/data_facts.py

test:
	$(PYTHON) -m pytest -q backend/tests

build:
	cd frontend && npm run build

start:
	PORT=$(PORT) ./scripts/start.sh

dev:
	./scripts/dev.sh

clean-runs:
	rm -rf runs
