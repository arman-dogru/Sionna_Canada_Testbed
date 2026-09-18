.PHONY: install test lint fetch normalize scene api web

install:
	python -m pip install -e ".[all,dev]"

test:
	python -m pytest

lint:
	python -m ruff check src tests

fetch:
	ottawa-rt fetch-data --width-m 2000

normalize:
	ottawa-rt normalize-ised --width-m 2000

scene:
	ottawa-rt build-scene --width-m 2000

api:
	ottawa-rt serve --reload

web:
	cd web && pnpm run dev
