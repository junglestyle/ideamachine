.PHONY: check standards sync-standards
SHELL := bash
ORG = https://github.com/junglestyle/.github

# The whole suite or nothing: without the database env vars three quarters of it would skip, and without the models
# extra the logreg tests would. Plain `uv run pytest` still runs whatever it can.
check:
	@for v in IM_DEV_ADMIN_URL IM_DATABASE_URL; do [ -n "$${!v}" ] || \
	{ echo "make check: $$v is not set, so the database tests would skip (export the IM_* lines of .env)" >&2; \
	exit 1; }; done
	@uv run --frozen python -c 'import sys; from importlib.util import find_spec; \
	sys.exit(" ".join(m for m in ("sklearn", "laya", "torch", "sentence_transformers", "gliner") if not find_spec(m)) or None)' || \
	{ echo "make check: the models extra is missing, so its tests would skip: uv sync --frozen --extra models" >&2; \
	exit 1; }
	uv run --frozen pytest -q -rs

standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp" && \
	"$$tmp/code-standards/sync.sh" check "$$tmp"

sync-standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp" && \
	"$$tmp/code-standards/sync.sh" sync "$$tmp" "$$(git -C "$$tmp" rev-parse HEAD)"
