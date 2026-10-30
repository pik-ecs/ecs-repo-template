# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "tomli_w",
#     "jinja2",
#     "pyyaml",
# ]
# ///
"""Render the template pyproject, merge it with the project's own, fix up src/."""

import keyword
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import jinja2
import tomli_w
import yaml

TEMPLATE_FILE = Path("pyproject.toml.jinja.src")


def read_answers() -> dict:
    """Read copier answers from .copier-answers.yml."""
    with Path(".copier-answers.yml").open() as f:
        return yaml.safe_load(f)


def render_template(answers: dict) -> dict:
    """Render ._pyproject.toml.jinja with copier answers."""
    template_text = TEMPLATE_FILE.read_text()
    template = jinja2.Template(template_text)
    rendered = template.render(**answers)
    return tomllib.loads(rendered)


def get_old_pyproject() -> dict | None:
    """Fetch pyproject.toml from git HEAD (for updates)."""
    try:
        with Path("pyproject.toml").open("rb") as f:
            return tomllib.load(f)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_package_name(dep: str) -> str:
    """Extract base package name from dependency string."""
    return (
        dep.split("[", maxsplit=1)[0]
        .split(">", maxsplit=1)[0]
        .split("<", maxsplit=1)[0]
        .split("=", maxsplit=1)[0]
        .split("!", maxsplit=1)[0]
    )


def merge_dependencies(new_deps: list[str], old_deps: list[str]) -> list[str]:
    """Merge dependency lists by package name (union)."""
    old_names = {get_package_name(d) for d in old_deps}
    extra = [d for d in new_deps if get_package_name(d) not in old_names]
    return list(old_deps) + extra


def merge_configs(new_config: dict, old_config: dict) -> dict:
    """Merge: take tool config from new, merge dependencies."""
    old_deps: list[str] = old_config.get("project", {}).get("dependencies", [])
    new_deps: list[str] = new_config.get("project", {}).get("dependencies", [])

    if old_deps:
        merged = merge_dependencies(new_deps, old_deps)
        new_config["project"]["dependencies"] = merged

    return new_config


def generate_normal_project_name(name: str) -> str:
    """Normalize project name to valid Python package name."""
    # Lowercase and replace spaces/dashes with underscores
    normalized = name.lower().replace("-", "_").replace(" ", "_")
    # Remove any non-alphanumeric and non-underscore characters
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    # Collapse multiple underscores
    normalized = re.sub(r"_+", "_", normalized)
    # Remove leading/trailing underscores
    normalized = normalized.strip("_")
    # Ensure doesn't start with digit
    if normalized and normalized[0].isdigit():
        normalized = f"pkg_{normalized}"
    # Avoid Python keywords
    if keyword.iskeyword(normalized):
        normalized = f"{normalized}_pkg"
    return normalized or "package"


def normalise_name(name: str, config: dict) -> None:
    """Normalise project name for package compatibility."""
    normalised_name = generate_normal_project_name(name)
    if "tool" in config and "hatch" in config["tool"]:
        hatch = config["tool"]["hatch"]
        if "build" in hatch and "targets" in hatch["build"]:
            hatch["build"]["targets"]["wheel"]["packages"] = [f"src/{normalised_name}"]

    old_path = Path(f"src/{name}")
    new_path = Path(f"src/{normalised_name}")
    if old_path.exists() and old_path != new_path:
        if new_path.exists():
            # On update, copier may re-create the raw-named dir; merge carefully.
            for p in sorted(old_path.rglob("*")):
                if p.is_file():
                    dest = new_path / p.relative_to(old_path)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(p), str(dest))
            try:
                old_path.rmdir()
            except OSError:
                print(f"warning: {old_path} still has files, not removing it")
        else:
            old_path.rename(new_path)
    new_path.mkdir(parents=True, exist_ok=True)
    init_file = new_path / "__init__.py"
    if not init_file.exists():
        init_file.touch()


def main() -> None:
    """Render, merge, write pyproject.toml, then delete the template sources."""
    # Read answers and render template
    answers = read_answers()
    new_config = render_template(answers)

    # Get old config from git (if update)
    old_config = get_old_pyproject()

    # Merge
    if old_config:
        new_config = merge_configs(new_config, old_config)

    normalise_name(answers["projectname"], new_config)

    # Write back
    with Path("pyproject.toml").open("wb") as f:
        tomli_w.dump(new_config, f)

    # Clean up
    TEMPLATE_FILE.unlink()
    Path(__file__).unlink()


if __name__ == "__main__":
    main()
