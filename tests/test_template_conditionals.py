"""Tests for template conditionals that branch on a choice variable.

Two defects shared one root cause: a Jinja2 conditional compared one
cookiecutter variable against a literal belonging to a *different*
variable's list of choices. Such a branch can never be taken, so the user
is silently routed into the fallback branch with no error reported.

``test_choice_comparisons_reference_a_valid_choice`` is the general guard
and catches any new occurrence anywhere in the template. The remaining
tests pin the two behaviours that were observed to be broken.
"""

import json
import re

from pathlib import Path

import pytest
import yaml

TEMPLATE_ROOT = Path(__file__).parent.parent / "src" / "scicookie"
PROJECT_TEMPLATE = TEMPLATE_ROOT / "{{cookiecutter.project_slug}}"
HOOKS = TEMPLATE_ROOT / "hooks"
REPO_ROOT = TEMPLATE_ROOT.parent.parent

EQUALITY = re.compile(
    r"cookiecutter\.([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:==|!=)\s*"
    r"([\"'])(.*?)\2"
)
MEMBERSHIP = re.compile(
    r"cookiecutter\.([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:not\s+in|in)\s*"
    r"\[([^\]]*)\]"
)
STRING_LITERAL = re.compile(r"[\"'](.*?)[\"']")


@pytest.fixture
def context():
    """Generate initial context for tests."""
    return {
        "author_full_name": "Sir Example",
        "author_email": "example@ex.ex",
        "project_name": "Example",
        "project_slug": "example",
        "project_short_description": "This is an example",
        "project_url": "example.com",
        "project_version": "0.1.0",
    }


def _choice_domains():
    """Return every cookiecutter variable that has a closed choice list."""
    config = json.loads((TEMPLATE_ROOT / "cookiecutter.json").read_text())
    return {
        name: values
        for name, values in config.items()
        if isinstance(values, list) and not name.startswith("_")
    }


def _template_files():
    """Yield every file in the hooks and in the project template."""
    for root in (HOOKS, PROJECT_TEMPLATE):
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


def _choice_comparisons():
    """Yield (path, line number, variable, literal) for each comparison."""
    for path in _template_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for name, _quote, literal in EQUALITY.findall(line):
                yield path, number, name, literal
            for name, body in MEMBERSHIP.findall(line):
                for literal in STRING_LITERAL.findall(body):
                    yield path, number, name, literal


def _publish_dir(workflow_path):
    """Return the publish_dir passed to the GitHub Pages action."""
    workflow = yaml.safe_load(workflow_path.read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "actions-gh-pages" in str(step.get("uses", "")):
                return step["with"]["publish_dir"]
    return None


def test_choice_comparisons_reference_a_valid_choice():
    """Reject conditionals whose literal is outside the variable's choices."""
    domains = _choice_domains()
    unreachable = []

    for path, number, name, literal in _choice_comparisons():
        if name not in domains or literal in domains[name]:
            continue
        unreachable.append(
            f"{path.relative_to(REPO_ROOT)}:{number}: "
            f"cookiecutter.{name} is compared with {literal!r}, "
            f"which is not one of {domains[name]}"
        )

    assert not unreachable, "unreachable conditional branch(es):\n" + (
        "\n".join(unreachable)
    )


@pytest.mark.parametrize(
    ("governance_document", "expected_content"),
    [
        ("numpy-governance", "numpy"),
        ("sciml-governance", "sciml"),
    ],
)
def test_selected_governance_document_is_installed(
    cookies, context, governance_document, expected_content
):
    """Install the chosen governance template as governance.md."""
    result = cookies.bake(
        extra_context={
            **context,
            "governance_document": governance_document,
        }
    )
    assert result.exit_code == 0
    assert result.exception is None

    governance = Path(result.project_path) / "governance.md"
    assert governance.is_file()
    assert expected_content in governance.read_text().lower()
    # the staging directory is always consumed
    assert not (Path(result.project_path) / "governance").exists()


def test_no_governance_document_when_none_is_selected(cookies, context):
    """Create no governance.md when the user selects None."""
    result = cookies.bake(
        extra_context={**context, "governance_document": "None"}
    )
    assert result.exit_code == 0
    assert result.exception is None
    assert not (Path(result.project_path) / "governance.md").exists()


@pytest.mark.parametrize(
    ("documentation_engine", "expected_publish_dir"),
    [
        ("mkdocs", "build/"),
        ("sphinx(rst)", "docs/_build/html/"),
        ("sphinx(myst)", "docs/_build/html/"),
        ("jupyter-book", "./docs/_build/html"),
    ],
)
def test_publish_dir_matches_documentation_engine(
    cookies, context, documentation_engine, expected_publish_dir
):
    """Point the release workflow at the engine's real output directory."""
    result = cookies.bake(
        extra_context={
            **context,
            "documentation_engine": documentation_engine,
            "use_github_actions": "yes",
        }
    )
    assert result.exit_code == 0
    assert result.exception is None

    workflow = (
        Path(result.project_path) / ".github" / "workflows" / "release.yaml"
    )
    assert workflow.is_file()
    assert _publish_dir(workflow) == expected_publish_dir


@pytest.mark.xfail(
    strict=True,
    reason=(
        "quarto has no publish_dir branch and docs-quarto/_quarto.yml "
        "declares no output-dir, so the correct value is still unknown"
    ),
)
def test_publish_dir_is_resolved_for_quarto(cookies, context):
    """Point the release workflow at a real directory for quarto too."""
    result = cookies.bake(
        extra_context={
            **context,
            "documentation_engine": "quarto",
            "use_github_actions": "yes",
        }
    )
    assert result.exit_code == 0

    workflow = (
        Path(result.project_path) / ".github" / "workflows" / "release.yaml"
    )
    publish_dir = _publish_dir(workflow)
    # a YAML folded scalar keeps "#" as content, so the placeholder
    # comment would reach the action as a literal path
    assert not publish_dir.lstrip().startswith("#")
