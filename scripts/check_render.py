"""Render the template pyproject with sample answers and assert fmt stability.

The rendered output is what generated projects keep, so its canonical form is
what must be stable: copier's three-way merge on `copier update` compares
re-renders byte-wise, and drift from pyproject-fmt shows up as conflicts.

Also asserts this repo and the template agree on shared tooling pins: the ruff
dev-dep version, the pyproject-fmt version (root dev dep vs template hook rev
comment), and the `entry` of any hook id the two pre-commit configs share.
"""

import difflib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import jinja2
import yaml

TEMPLATE = Path("template/pyproject.toml.jinja")
TEMPLATE_HOOKS = Path("template/.pre-commit-config.yaml.jinja")
ROOT_PYPROJECT = Path("pyproject.toml")
ROOT_HOOKS = Path(".pre-commit-config.yaml")

REV_COMMENT = re.compile(r'rev:\s*"?[0-9a-f]{40}"?\s*#v?([\w.]+)')

ANSWERS = {
    "projectname": "demo-project",
    "packagename": "demo_project",
    "project_description": "demo project",
    "python_version": "3.13",
    "use_dvc": True,
    "use_vscode": True,
}


def _pin(dep: str) -> tuple[str, str | None]:
    """Split 'ruff>=0.15.22' into (name, version); version is None if unpinned."""
    match = re.fullmatch(r"(?P<name>[\w.-]+)(?:(?:==|>=)(?P<version>[\w.]+))?", dep)
    if match is None:
        msg = f"unexpected dev-dep form: {dep!r}"
        raise AssertionError(msg)
    return match["name"], match["version"]


def _dev_versions(pyproject_text: str) -> dict[str, str]:
    """Map dependency name to pinned version over a project's dev group."""
    dev = tomllib.loads(pyproject_text)["dependency-groups"]["dev"]
    return {name: version for name, version in map(_pin, dev) if version is not None}


def _hook_entries(config_text: str) -> dict[str, str]:
    """Map hook id to entry over a pre-commit config; skip hooks without one."""
    entries = {}
    for repo in yaml.safe_load(config_text)["repos"]:
        entries.update(
            {h["id"]: h["entry"] for h in repo.get("hooks", []) if "entry" in h}
        )
    return entries


def check_drift(rendered_pyproject: str, rendered_hooks: str) -> list[str]:
    """Return human-readable drift messages between this repo and the template."""
    problems: list[str] = []

    root_dev = _dev_versions(ROOT_PYPROJECT.read_text())
    template_dev = _dev_versions(rendered_pyproject)
    for name in ("ruff", "ty"):
        root, template = root_dev.get(name), template_dev.get(name)
        if root is not None and template is not None and root != template:
            problems.append(f"{name} version drift: root {root}, template {template}")

    root_fmt = root_dev.get("pyproject-fmt")
    rev_comment = REV_COMMENT.search(rendered_hooks)
    if (
        root_fmt is not None
        and rev_comment is not None
        and root_fmt != rev_comment.group(1)
    ):
        problems.append(
            f"pyproject-fmt version drift: root {root_fmt}, "
            f"template rev v{rev_comment.group(1)}"
        )

    root_hooks = _hook_entries(ROOT_HOOKS.read_text())
    template_hooks = _hook_entries(rendered_hooks)
    problems.extend(
        f"hook {hook_id!r} entry drift: root {root_hooks[hook_id]!r}, "
        f"template {template_hooks[hook_id]!r}"
        for hook_id in sorted(root_hooks.keys() & template_hooks.keys())
        if root_hooks[hook_id] != template_hooks[hook_id]
    )
    return problems


def _render(path: Path) -> str:
    """Render a template file with the sample answers."""
    env = jinja2.Environment(keep_trailing_newline=True)  # noqa: S701  # TOML text, not HTML
    return env.from_string(path.read_text()).render(**ANSWERS)


def check_render(rendered: str) -> int:
    """Parse and reformat-check the rendered template pyproject; 1 on drift."""
    tomllib.loads(rendered)  # fail loudly if the Jinja produces invalid TOML

    exe = shutil.which("pyproject-fmt")
    if exe is None:
        print(
            "pyproject-fmt not found; run via `uv run scripts/check_render.py`",
            file=sys.stderr,
        )
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "pyproject.toml"
        target.write_text(rendered)
        # pyproject-fmt exits 1 when it reformats (the file is written either way).
        result = subprocess.run([exe, str(target)], capture_output=True, check=False)
        if result.returncode > 1:
            print(result.stderr.decode(), file=sys.stderr)
            result.check_returncode()
        formatted = target.read_text()

    if formatted != rendered:
        diff = difflib.unified_diff(
            rendered.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile="rendered",
            tofile="pyproject-fmt canonical",
        )
        sys.stdout.writelines(diff)
        return 1
    return 0


def main() -> int:
    """Run the render and pin-parity checks; return non-zero on any failure."""
    status = check_render(_render(TEMPLATE))
    for problem in check_drift(_render(TEMPLATE), _render(TEMPLATE_HOOKS)):
        print(f"drift: {problem}", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
