# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Apply the group-standard "protect main" ruleset to this project's GitHub repository.

Run from anywhere inside the project; the repository is detected from the ``origin``
remote:

    uv run .github/rulesets/protect.py [--reviews N]

The script is declarative: the ruleset applied is exactly what the command says, so
re-running without ``--reviews`` resets the required review count to the group standard
(0). The JSON file next to this script is never modified.

Settings are sent to GitHub's GraphQL API, the same interface the repository Settings UI
uses. (The REST rulesets endpoints were tested first and found broken on this org: they
silently drop the branch condition on create and return 404 on update. GraphQL also
resolves renamed orgs natively, so remotes still using the old 'mcc-apsis' URL work
as-is.)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

AUTH_HELP = """\
No GitHub token found. Either:
  - set the GITHUB_TOKEN environment variable to a fine-grained personal access token
    (create one at https://github.com/settings/personal-access-tokens/new for this
    repository, with the repository permission "Administration: write"), or
  - install the GitHub CLI (https://cli.github.com/) and run `gh auth login`."""

FIND_REPO = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    rulesets(first: 20) { nodes { id name } }
  }
}
"""
CREATE = """
mutation($input: CreateRepositoryRulesetInput!) {
  createRepositoryRuleset(input: $input) { ruleset { databaseId name } }
}
"""
UPDATE = """
mutation($input: UpdateRepositoryRulesetInput!) {
  updateRepositoryRuleset(input: $input) { ruleset { databaseId name } }
}
"""

GUARD_BLOCK = """{indent}- repo: https://github.com/pre-commit/pre-commit-hooks
{indent}  rev: 3e8a8703264a2f4a69428a0aa4dcb512790b2c8c # v6.0.0
{indent}  hooks:
{indent}    - id: no-commit-to-branch
"""


def fail(message: str) -> None:
    """Print a fatal error message and exit."""
    print(message)
    if "upgrade" in message.lower():
        print(
            "Branch protection (rulesets) is unavailable for private repositories on "
            "the GitHub Free plan. Make the repository public, or ask about an org "
            "upgrade."
        )
    sys.exit(1)


def github_graphql(query: str, variables: dict, token: str) -> dict:
    """Send one GraphQL query to GitHub and return the 'data' field."""
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ecs-repo-template protect.py",  # GitHub requires this header
        },
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 (constant URL)
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("message", error.reason)
        except (json.JSONDecodeError, OSError):
            detail = error.reason
        fail(f"GitHub returned HTTP {error.code}: {detail}")
    if result.get("errors"):
        fail(f"GitHub rejected the request: {result['errors'][0]['message']}")
    return result["data"]


def find_github_token() -> str:
    """Use $GITHUB_TOKEN, or the GitHub CLI's token if gh happens to be installed."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    if shutil.which("gh"):
        gh = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False
        )
        if gh.returncode == 0 and gh.stdout.strip():
            return gh.stdout.strip()
    sys.exit(AUTH_HELP)


def repo_from_origin_remote() -> tuple[str, str]:
    """Read 'owner' and 'name' from the origin remote, so no arguments are needed."""
    git = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if git.returncode != 0:
        fail(
            "No 'origin' git remote found. Create and push the GitHub repository "
            "first, e.g.:\n  gh repo create <name> --source . --push"
        )
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", git.stdout.strip())
    if not match or "/" not in match.group(1):
        fail(f"The 'origin' remote is not a GitHub repository: {git.stdout.strip()}")
    owner, name = match.group(1).split("/", 1)
    return owner, name


def find_precommit_config() -> Path | None:
    """Locate .pre-commit-config.yaml.

    The git root is checked first (so the template repo itself, whose config sits above
    the shipped script, works too), then the directory the script ships in.
    """
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    candidates = []
    if toplevel.returncode == 0:
        candidates.append(Path(toplevel.stdout.strip()) / ".pre-commit-config.yaml")
    candidates.append(Path(__file__).parents[2] / ".pre-commit-config.yaml")
    return next((candidate for candidate in candidates if candidate.exists()), None)


def ensure_commit_guard() -> None:
    """Add the no-commit-to-branch guard to .pre-commit-config.yaml if it isn't there.

    The file is appended to, never rewritten by a YAML round trip, so comments and
    formatting in the rest of the file survive; the new entry copies the file's own
    indentation style.
    """
    config = find_precommit_config()
    manual = "add the block manually:\n" + GUARD_BLOCK.format(indent="  ")
    if config is None:
        print(
            f"No .pre-commit-config.yaml found in the git root or the project "
            f"directory; {manual}"
        )
        return
    content = config.read_text()
    if "no-commit-to-branch" in content:
        print("Pre-commit guard already present.")
        return
    style = re.search(r"(?m)^(\s*)- repo:", content)
    if not style:
        print(f"Could not recognise .pre-commit-config.yaml's format; {manual}")
        return
    separator = "" if content.endswith("\n") else "\n"
    config.write_text(
        content + separator + "\n" + GUARD_BLOCK.format(indent=style.group(1))
    )
    print(f"Added no-commit-to-branch guard to {config} (remember to git add it).")


def main() -> int:
    """Apply the ruleset upstream and the commit guard locally."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--reviews",
        type=int,
        metavar="N",
        help="require N approving reviews before merging into main "
        "(default: the group standard of 0, i.e. PRs required but no review gate)",
    )
    args = parser.parse_args()

    standard = json.loads((Path(__file__).parent / "main-protection.json").read_text())
    if args.reviews is not None:
        for rule in standard["rules"]:
            if rule["type"] == "PULL_REQUEST":
                parameters = rule.setdefault("parameters", {}).setdefault(
                    "pullRequest", {}
                )
                parameters["requiredApprovingReviewCount"] = args.reviews

    ensure_commit_guard()  # local half of the policy; runs without network or a token
    token = find_github_token()
    owner, name = repo_from_origin_remote()

    repository = github_graphql(FIND_REPO, {"owner": owner, "name": name}, token)[
        "repository"
    ]
    if repository is None:
        fail(f"GitHub has no repository '{owner}/{name}' your token can see.")

    already = next(
        (
            ruleset
            for ruleset in repository["rulesets"]["nodes"]
            if ruleset["name"] == standard["name"]
        ),
        None,
    )
    if already:
        action = "Updated"
        response = github_graphql(
            UPDATE, {"input": {"repositoryRulesetId": already["id"], **standard}}, token
        )["updateRepositoryRuleset"]
    else:
        action = "Created"
        response = github_graphql(
            CREATE, {"input": {"sourceId": repository["id"], **standard}}, token
        )["createRepositoryRuleset"]

    reviews = args.reviews if args.reviews is not None else 0
    ruleset = response["ruleset"]
    print(
        f"{action} ruleset '{ruleset['name']}' on {owner}/{name} "
        f"(id {ruleset['databaseId']}, required reviews: {reviews})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
