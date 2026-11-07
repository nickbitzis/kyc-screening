.PHONY: install test lint eval gate bench rescreen run docker

install:
	uv sync --python 3.12

test:
	uv run pytest -q

lint:
	uvx ruff check .

eval:
	uv run kycscreen eval

gate:
	uv run kycscreen eval --gate

bench:
	uv run kycscreen bench

rescreen:
	uv run kycscreen rescreen

run:
	uv run kycscreen serve --port 8000

docker:
	docker build -t kycscreen .
	docker run --rm -p 8000:8000 kycscreen
