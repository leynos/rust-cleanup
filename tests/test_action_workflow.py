"""Behavioural checks for composite action validation."""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - integration tests drive the installed Actionlint.
from pathlib import Path

import pytest
import yaml

from scripts.action_workflow import action_workflow, app, load_action


def test_action_adapter_preserves_shell_steps_and_inputs() -> None:
    """Shell and expression diagnostics must survive the workflow adapter."""
    action = {
        "name": "Example",
        "inputs": {"output": {"description": "Output directory", "default": "dist"}},
        "runs": {
            "using": "composite",
            "steps": [{"shell": "bash", "run": 'echo "${{ inputs.output }}"'}],
        },
    }
    workflow_text = action_workflow(action)
    why = "Composite action projection must return workflow YAML"
    assert workflow_text is not None, why
    workflow = yaml.safe_load(workflow_text)
    expected_inputs = {
        "description": "Output directory",
        "default": "dist",
        "type": "string",
    }
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["output"] == expected_inputs, "Actionlint must see the action inputs"
    expected_steps = [{"shell": "bash", "run": 'echo "${{ inputs.output }}"'}]
    steps = workflow["jobs"]["action"]["steps"]
    assert steps == expected_steps, "Actionlint must see unchanged executable steps"


def test_action_adapter_cli_emits_parseable_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The actual CLI must emit only the workflow YAML on standard output."""
    path = tmp_path / "action.yml"
    path.write_text(
        "name: Example\nruns:\n  using: composite\n  steps: []\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exit_info:
        app([str(path)])
    assert exit_info.value.code == 0, "Rendering valid YAML must exit successfully"
    workflow = yaml.safe_load(capsys.readouterr().out)
    why = "CLI output must be valid workflow YAML"
    assert workflow["jobs"]["action"]["steps"] == [], why


@pytest.mark.parametrize(
    ("action_text", "error_context"),
    [
        pytest.param(None, "No such file", id="missing-file"),
        pytest.param("name: [\n", "YAML", id="invalid-yaml"),
        pytest.param("[]\n", "action definition", id="invalid-root"),
        pytest.param(
            "runs:\n  using: composite\n  steps: []\n", "name", id="missing-name"
        ),
        pytest.param(
            "name: 12\nruns:\n  using: composite\n  steps: []\n",
            "name",
            id="invalid-name",
        ),
        pytest.param("name: Example\n", "runs", id="missing-runs"),
        pytest.param("name: Example\nruns: []\n", "runs", id="invalid-runs"),
        pytest.param("name: Example\nruns: {}\n", "using", id="missing-runtime"),
        pytest.param(
            "name: Example\nruns:\n  using: composite\n  steps: []\ninputs: []\n",
            "inputs",
            id="invalid-inputs",
        ),
        pytest.param(
            "name: Example\nruns:\n  using: composite\n  steps: []\n"
            "inputs:\n  1:\n    description: Numeric input name\n",
            "inputs keys",
            id="invalid-input-name",
        ),
        pytest.param(
            "name: Example\nruns:\n  using: composite\n  steps: []\n"
            "inputs:\n  output: text\n",
            "inputs.output",
            id="invalid-input-definition",
        ),
        pytest.param(
            "name: Example\nruns:\n  using: composite\n  steps: text\n",
            "steps",
            id="invalid-steps",
        ),
    ],
)
def test_action_adapter_cli_reports_action_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action_text: str | None,
    error_context: str,
) -> None:
    """The CLI reports file, YAML, and action-shape errors without tracebacks."""
    path = tmp_path / "action.yml"
    if action_text is not None:
        path.write_text(action_text, encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        app([str(path)])

    why = f"Invalid action input must exit with status 1: {path}"
    assert exit_info.value.code == 1, why
    output = capsys.readouterr()
    stderr = output.err
    why = f"CLI diagnostics must identify the action path: {stderr}"
    assert str(path) in stderr, why
    why = f"CLI diagnostics must identify the input error: {stderr}"
    assert error_context.casefold() in stderr.casefold(), why
    why = f"CLI diagnostics must not include a traceback: {stderr}"
    assert "Traceback" not in stderr, why
    why = f"CLI errors must not write workflow output: {output.out}"
    assert not output.out, why


@pytest.mark.parametrize(
    "action_text",
    [
        pytest.param(
            "name: Node action\nruns:\n  using: node20\n  main: index.js\n",
            id="node20",
        ),
        pytest.param(
            "name: Docker action\nruns:\n  using: docker\n  image: Dockerfile\n",
            id="docker",
        ),
    ],
)
def test_action_adapter_cli_does_not_project_noncomposite_actions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action_text: str,
) -> None:
    """Non-composite metadata validation remains with action-validator."""
    path = tmp_path / "action.yml"
    path.write_text(action_text, encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        app([str(path)])

    why = "Valid non-composite action metadata must exit successfully"
    assert exit_info.value.code == 0, why
    output = capsys.readouterr()
    why = "The adapter must emit no workflow for a non-composite action"
    assert not output.out, why
    why = "The adapter must not report valid non-composite metadata as an error"
    assert not output.err, why


@pytest.mark.timeout(180)
def test_action_adapter_runs_outside_project_environment(tmp_path: Path) -> None:
    """The adapter's uv metadata must supply dependencies outside the project."""
    adapter = tmp_path / "action_workflow.py"
    shutil.copyfile(Path(__file__).parents[1] / "scripts/action_workflow.py", adapter)
    action = tmp_path / "action.yml"
    action.write_text(
        "name: Example\nruns:\n  using: composite\n  steps: []\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    uv = shutil.which("uv")
    assert uv is not None, "Standalone script validation requires uv on PATH"
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - resolved uv command and generated fixture, no shell.
        [
            uv,
            "run",
            "--no-project",
            "--python",
            "3.14",
            "--script",
            str(adapter),
            str(action),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        timeout=120,
    )
    why = f"Standalone uv script must succeed: {result.stderr}"
    assert result.returncode == 0, why
    workflow = yaml.safe_load(result.stdout)
    why = "Standalone action adapter must preserve an empty composite step list"
    assert workflow["jobs"]["action"]["steps"] == [], why


@pytest.mark.parametrize("is_valid", [True, False], ids=["valid", "invalid-input"])
def test_actionlint_checks_adapted_action_expressions(
    tmp_path: Path, *, is_valid: bool
) -> None:
    """The real linter must reject action expressions referencing missing inputs."""
    path = tmp_path / "action.yml"
    input_name = "output" if is_valid else "missing"
    path.write_text(
        "name: Example\n"
        "inputs:\n"
        "  output:\n"
        "    description: Output directory\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        f'      run: echo "${{{{ inputs.{input_name} }}}}"\n',
        encoding="utf-8",
    )
    workflow = tmp_path / "workflow.yml"
    workflow_text = action_workflow(load_action(path))
    why = "Composite action projection must return workflow YAML"
    assert workflow_text is not None, why
    workflow.write_text(workflow_text, encoding="utf-8")
    actionlint = shutil.which("actionlint")
    if actionlint is None:
        pytest.skip("The Actionlint integration check requires actionlint on PATH")
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - resolved detector and generated fixture, no shell.
        [actionlint, str(workflow)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    why = f"Actionlint must validate composite expressions: {result.stdout}"
    assert (result.returncode == 0) == is_valid, why
