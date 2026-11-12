#!/usr/bin/env -S uv run --script
# Match the repository's Python 3.14 lint and CI runtime.
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "cyclopts==4.25.2",
#     "pyyaml==6.0.3",
# ]
# ///
"""Expose composite action steps to actionlint's workflow checks.

The adapter preserves action inputs and steps; action-validator owns metadata
validation. It is used only by ``make lint-actions`` and is not a runtime API.
"""

import collections.abc as cabc
import sys
import typing as typ
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import] - Cyclopts resolves the runtime CLI annotation.

import cyclopts
import yaml

app = cyclopts.App()


def load_action(path: Path) -> object:
    """Read and parse an action manifest from disk.

    For example, ``load_action(Path("action.yml"))`` returns the parsed YAML
    value so callers can validate it before projecting any workflow steps.

    Returns
    -------
    object
        The value parsed from the action manifest.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _require_string_mapping(
    value: object, field_name: str
) -> cabc.Mapping[str, object]:
    """Return a mapping with string keys for the workflow projection.

    For example, ``_require_string_mapping({"using": "composite"}, "runs")``
    returns the ``runs`` mapping unchanged.

    Returns
    -------
    cabc.Mapping[str, object]
        The validated mapping without altering its values.

    Raises
    ------
    TypeError
        If the value is not a mapping.
    ValueError
        If the mapping has keys that are not strings.
    """
    if not isinstance(value, cabc.Mapping):
        message = f"{field_name} must be a mapping"
        raise TypeError(message)
    if not all(isinstance(key, str) for key in value):
        message = f"{field_name} keys must be strings"
        raise ValueError(message)
    return typ.cast("cabc.Mapping[str, object]", value)


def _workflow_inputs(value: object) -> dict[str, dict[str, object]]:
    """Add workflow input types to validated action input definitions.

    For example, ``_workflow_inputs({"output": {"default": "dist"}})`` adds
    ``type: string`` to the preserved ``output`` input definition.

    Returns
    -------
    dict[str, dict[str, object]]
        Input definitions suitable for a manually dispatched workflow.
    """
    inputs = _require_string_mapping(value, "inputs")
    return {
        name: {
            **_require_string_mapping(definition, f"inputs.{name}"),
            "type": "string",
        }
        for name, definition in inputs.items()
    }


def action_workflow(action_value: object) -> str | None:
    """Render a composite action as a workflow, or skip other action types.

    For example, a parsed composite action returns workflow YAML for Actionlint,
    while a parsed ``using: docker`` action returns ``None`` for the CLI to skip.

    Returns
    -------
    str | None
        Workflow YAML for a composite action, or ``None`` for another runtime.

    Raises
    ------
    TypeError
        If the parsed value lacks the shape needed for the projection.

    Notes
    -----
    The mapping validator raises ``ValueError`` for keys that are not strings.
    """
    action = _require_string_mapping(action_value, "action definition")
    name = action.get("name")
    if not isinstance(name, str):
        message = "action name must be a string"
        raise TypeError(message)

    runs = _require_string_mapping(action.get("runs"), "runs")
    using = runs.get("using")
    if not isinstance(using, str):
        message = "runs.using must be a string"
        raise TypeError(message)
    if using != "composite":
        return None

    steps = runs.get("steps")
    if not isinstance(steps, list):
        message = "composite runs.steps must be a list"
        raise TypeError(message)

    workflow = {
        "name": name,
        "on": {
            "workflow_dispatch": {"inputs": _workflow_inputs(action.get("inputs", {}))}
        },
        "jobs": {"action": {"runs-on": "ubuntu-latest", "steps": steps}},
    }
    return yaml.safe_dump(workflow, sort_keys=False)


@app.default
def main(path: Path) -> None:
    """Print a composite workflow or report a bounded input diagnostic.

    For example, ``uv run --script scripts/action_workflow.py action.yml`` emits
    YAML that can be piped to ``actionlint -``; valid Node and Docker actions
    produce no output because action-validator owns their metadata checks.

    Raises
    ------
    SystemExit
        With status 1 after reporting a file, YAML, or action-shape error.
    """
    try:
        workflow = action_workflow(load_action(path))
    except (OSError, yaml.YAMLError, TypeError, ValueError) as error:
        match error:
            case yaml.YAMLError():
                kind = "invalid YAML"
            case TypeError() | ValueError():
                kind = "invalid action definition"
            case _:
                kind = "cannot read action file"
        print(f"{path}: {kind}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if workflow is not None:
        print(workflow, end="")


if __name__ == "__main__":
    app()
