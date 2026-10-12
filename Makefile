.PHONY: install test build run
install:
	python -m pip install -e '.[test]'
	npm --prefix frontend ci
test:
	python -m pytest -q
build:
	npm --prefix frontend run build
run:
	python run.py
