"""Render the template pyproject with sample answers and assert fmt stability.

The rendered output is what generated projects keep, so its canonical form is
what must be stable: copier's three-way merge on `copier update` compares
re-renders byte-wise, and drift from pyproject-fmt shows up as conflicts.
"""

import difflib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import jinja2

TEMPLATE = Path("template/pyproject.toml.jinja")

ANSWERS = {
    "projectname": "demo-project",
    "packagename": "demo_project",
    "project_description": "demo project",
    "python_version": "3.13",
    "use_dvc": True,
    "use_vscode": True,
}


def main() -> int:
    """Render, parse, and reformat-check; return non-zero with a diff on drift."""
    env = jinja2.Environment(keep_trailing_newline=True)  # noqa: S701  # TOML text, not HTML
    rendered = env.from_string(TEMPLATE.read_text()).render(**ANSWERS)
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


if __name__ == "__main__":
    raise SystemExit(main())
