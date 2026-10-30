"""Fail if any DVC-tracked file is missing from the remote it belongs to."""

import json
import subprocess
import sys


def main() -> int:
    """Return 1 if any DVC-tracked file is missing from its remote."""
    result = subprocess.run(
        [sys.executable, "-m", "dvc", "data", "status", "--not-in-remote", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("Could not check the DVC remote, so cannot verify data is pushed:")
        print(result.stderr.strip())
        return 1

    try:
        missing: list[str] = json.loads(result.stdout or "{}").get("not_in_remote", [])
    except json.JSONDecodeError:
        print(f"Unexpected output from `dvc data status`: {result.stdout!r}")
        return 1

    if missing:
        print("DVC data has not been pushed to its remote yet:")
        for path in missing:
            print(f"  {path}")
        print("Run `uv run dvc push`, then commit again.")
        print("If the remote is genuinely unreachable and you know this is safe:")
        print("  SKIP=dvc-pushed git commit ...")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
