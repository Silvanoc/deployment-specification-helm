install:
    uv sync --locked

generate:
    uv run --locked python tools/generate.py

validate:
    uv run --locked python tools/check_examples.py

test:
    uv run --locked python -m unittest discover -s tests -v

check: test validate
    uv run --locked python tools/generate.py --check
