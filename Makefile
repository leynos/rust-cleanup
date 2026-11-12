MDLINT ?= markdownlint-cli2
NIXIE ?= nixie
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_VERSION ?= 0.6.1
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
ACTIONLINT ?= actionlint
ACTION_VALIDATOR ?= action-validator
export PATH := $(HOME)/.local/bin:$(HOME)/.bun/bin:$(PATH)
UV ?= $(shell command -v uv 2>/dev/null || printf '%s/.local/bin/uv' "$$HOME")
USER_CARGO := $(HOME)/.cargo/bin/cargo
USER_WHITAKER := $(HOME)/.local/bin/whitaker
USER_BIN_PATH := $(HOME)/.cargo/bin:$(HOME)/.local/bin:$(HOME)/.bun/bin
TOOLS = $(MDTABLEFIX) $(MDLINT) $(ACTIONLINT) $(ACTION_VALIDATOR)
VENV_TOOLS = pytest
UV_ENV = PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = env $(UV_ENV) $(UV) tool run --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder
WITH_ACT ?= 0
ACT_TEST_ENV = $(if $(filter 1 true yes on,$(WITH_ACT)),RUN_ACT_VALIDATION=1,)
PYTEST_XDIST_WORKERS ?= auto
# Discover tracked and new Python sources, including hidden action directories.
PYTHON_TARGETS_FROM_GIT := $(if $(filter undefined,$(origin PYTHON_TARGETS)),1,0)
PYTHON_TARGETS ?=
RUFF_VERSION ?= 0.16.8
TY_VERSION ?= 0.0.56
LINT_PYTHON ?= 3.14
RUFF = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from 'ruff==$(RUFF_VERSION)' ruff
TY = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from 'ty==$(TY_VERSION)' ty
DF12_PYTHON_LINTS_REF ?= v0.4.0
DF12_PYTHON_LINTS = git+https://github.com/leynos/df12-python-lints.git@$(DF12_PYTHON_LINTS_REF)
DF12_PYLINT_MESSAGES = R9101,C9102,R9103,R9104,C9105,C9106,C9107,R9108,R9109,R9110,R9111,R9112,C9112
DF12_PYLINT = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' --with '$(DF12_PYTHON_LINTS)' pylint --disable=all --load-plugins=df12_python_lints --enable=syntax-error,$(DF12_PYLINT_MESSAGES)
AMBRLEAKS = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from '$(DF12_PYTHON_LINTS)' ambrleaks
SKYLOS = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from 'df12-python-lints[skylos] @ $(DF12_PYTHON_LINTS)' df12-skylos
DUPLICATION_GATE = $(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from '$(DF12_PYTHON_LINTS)' df12-duplication
# Pylint runs on CPython at the project baseline: no managed PyPy parses
# Python 3.14 syntax.
PYLINT_PYTHON ?= cpython@3.14
PYLINT_VERSION ?= 4.0.9
PYLINT_TARGETS ?= $(PYTHON_TARGETS)
PYLINT = $(UV_ENV) $(UV) tool run --managed-python --python $(PYLINT_PYTHON) --from 'pylint==$(PYLINT_VERSION)' pylint


.PHONY: help all audit clean build build-release lint lint-python fmt check-fmt \
        check-python-sources \
        markdownlint nixie spelling test typecheck dead-code duplication install-nose lint-actions validate $(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all
# Shared build caches require sequential gates even when make receives -j.
.NOTPARALLEL:

all: check-python-sources build check-fmt lint typecheck test
	+$(MAKE) spelling

define ensure_uv
	@command -v $(UV) >/dev/null 2>&1 || { \
	  printf "Error: uv is required, but '%s' was not found or is not executable\n" "$(UV)" >&2; \
	  exit 1; \
	}
endef

.venv: pyproject.toml
	$(call ensure_uv)
	$(UV_ENV) $(UV) venv --clear

build: .venv ## Build virtual-env and install deps
	$(UV_ENV) $(UV) sync --group dev

build-release: ## Build artefacts (sdist & wheel)
	$(call ensure_uv)
	$(UV_ENV) $(UV) run python -m build --sdist --wheel

clean: ## Remove build artefacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv .uv-cache .uv-tools \
	  .typos-oxendict-base.json .typos-oxendict-base.toml
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	@$(UV_ENV) $(UV) run which $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required in the virtualenv, but is not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

ifneq ($(strip $(TOOLS)),)
$(TOOLS): ## Verify required CLI tools
	$(call ensure_tool,$@)
endif


ifneq ($(strip $(VENV_TOOLS)),)
.PHONY: $(VENV_TOOLS)
$(VENV_TOOLS): build ## Verify required CLI tools in venv
	$(call ensure_tool_venv,$@)
endif


ifeq ($(PYTHON_TARGETS_FROM_GIT),1)
# Recipe expansion runs once, after prerequisites; check this capture's status
# before consumers expand their shared, simply expanded source list.
check-python-sources:
	$(eval PYTHON_TARGETS := $(shell git ls-files --cached --others --exclude-standard '*.py' '*.pyi'))
	$(if $(filter 0,$(.SHELLSTATUS)),,$(error Error: git ls-files failed while discovering Python sources for PYTHON_TARGETS))
	@:
else
check-python-sources:
	@:
endif

fmt: check-python-sources build $(MDTABLEFIX) ## Format sources
	$(RUFF) format $(PYTHON_TARGETS)
	$(RUFF) check --select I --fix $(PYTHON_TARGETS)

	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

check-fmt: check-python-sources build $(MDTABLEFIX) ## Verify formatting
	$(RUFF) format --check $(PYTHON_TARGETS)

	@test "$$($(MDTABLEFIX) --version)" = "mdtablefix $(MDTABLEFIX_VERSION)" || { \
	  printf 'Error: mdtablefix %s is required\n' '$(MDTABLEFIX_VERSION)' >&2; exit 1; \
	}
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

lint: lint-python dead-code duplication lint-actions ## Run linters

lint-python: check-python-sources build ## Run Python linters
	$(RUFF) check $(PYTHON_TARGETS)
	$(UV_ENV) $(UV) run interrogate --fail-under 100 $(PYTHON_TARGETS)
	$(PYLINT) $(PYLINT_TARGETS)
	$(DF12_PYLINT) $(PYTHON_TARGETS)
	$(AMBRLEAKS) tests


typecheck: check-python-sources build ## Run typechecking
	$(TY) --version
	$(TY) check $(PYTHON_TARGETS)

audit: build ## Audit dependencies for known vulnerabilities
	$(UV_ENV) $(UV) run pip-audit


markdownlint: $(MDLINT) ## Lint Markdown files and spelling
	env -u NO_COLOR $(MDLINT) '**/*.md'
	+$(MAKE) spelling

spelling: ## Enforce en-GB-oxendict spelling
	$(TYPOS_CONFIG_BUILDER) gate --repository . --scope all

nixie: ## Validate Mermaid diagrams
	$(call ensure_tool,$(NIXIE))
	$(NIXIE) --no-sandbox

test: build $(VENV_TOOLS) ## Run tests
	$(UV_ENV) $(ACT_TEST_ENV) $(UV) run pytest -v -n $(PYTEST_XDIST_WORKERS)


validate: ## Validate the Makefile
	mbake validate Makefile

dead-code: ## Run the shared strict Skylos gate
	$(SKYLOS) check --repository .
	$(UV_ENV) $(UV) tool run --python $(LINT_PYTHON) --from 'vulture==2.16' vulture tests

install-nose: ## Install the pinned detector with the shared verified installer
	$(DUPLICATION_GATE) install --repository .

duplication: install-nose ## Check every duplication family with the shared gate
	$(DUPLICATION_GATE) check --repository .

lint-actions: build $(ACTIONLINT) $(ACTION_VALIDATOR) ## Validate workflows and local actions
	$(call ensure_tool,shellcheck)
	$(UV_ENV) $(UV) tool run --from 'yamllint==1.38.0' yamllint --strict .github
	$(ACTIONLINT)
	@set -eu; tmpdir=$$(mktemp -d /tmp/template-actions-XXXXXX); \
	workflow="$$tmpdir/workflow.yml"; actions="$$tmpdir/actions"; \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	find .github/actions -type f \( -name action.yml -o -name action.yaml \) -print > "$$actions"; \
	while IFS= read -r action; do \
	  $(ACTION_VALIDATOR) "$$action"; \
	  $(UV_ENV) $(UV) run --script scripts/action_workflow.py "$$action" > "$$workflow"; \
	  if [ -s "$$workflow" ]; then \
	    $(ACTIONLINT) -stdin-filename "$$action" - < "$$workflow"; \
	  fi; \
	done < "$$actions"

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":.*##"; printf "Available targets:\n"} {gsub(/^[[:space:]]+/, "", $$2); printf "  %-20s %s\n", $$1, $$2}'
