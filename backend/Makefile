.PHONY: test test-unit test-integration test-e2e test-contract coverage lint typecheck security

test:
	pytest backend/api/tests -v

test-unit:
	pytest backend/api/tests -m unit -v

test-integration:
	pytest backend/api/tests -m integration -v

test-e2e:
	pytest backend/api/tests -m e2e -v

test-contract:
	pytest backend/api/tests -m contract -v

coverage:
	pytest backend/api/tests --cov=backend/api --cov-report=term-missing --cov-report=html

lint:
	ruff check backend

typecheck:
	mypy backend/api

security:
	bandit -r backend/api -q
	pip-audit -r backend/requirements.txt
