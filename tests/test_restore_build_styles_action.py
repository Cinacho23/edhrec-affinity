"""Execute the action's artifact-listing pipeline with GitHub CLI semantics."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ACTION = Path(__file__).resolve().parents[1] / ".github/actions/restore-build-styles/action.yml"
pytestmark = pytest.mark.skipif(not shutil.which("jq") or not shutil.which("bash"), reason="Requires bash and jq")


def listing_command():
    # Exercise the actual shell command, including its pipeline and redirection.
    script = textwrap.dedent(ACTION.read_text().split("      run: |\n", 1)[1])
    start = script.index("gh api --paginate --slurp")
    return script[start:script.index("while IFS=", start)].strip()


def run_listing(tmp_path, pages, *, api_failure=False):
    executable = tmp_path / "gh"
    executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
        import os, sys
        args = sys.argv[1:]
        if "--slurp" in args and any(flag in args for flag in ("--jq", "--template")):
            sys.exit("the --slurp option is not supported with --jq or --template")
        if os.environ.get("TEST_API_FAILURE") == "1":
            sys.exit(7)
        if args[:3] != ["api", "--paginate", "--slurp"]:
            sys.exit("Pagination must remain enabled")
        print(os.environ["TEST_ARTIFACT_PAGES"])
    '''))
    executable.chmod(0o755)
    destination = tmp_path / "candidates.tsv"
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", listing_command()],
                            env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                                 "GITHUB_REPOSITORY": "owner/repo", "candidates": str(destination),
                                 "TEST_ARTIFACT_PAGES": json.dumps(pages),
                                 "TEST_API_FAILURE": "1" if api_failure else "0"},
                            text=True, capture_output=True, check=False)
    return result, destination.read_text()


def test_artifacts_are_sorted_across_pages_without_incompatible_gh_formatting(tmp_path):
    def artifact(run_id, date, *, expired=False, name=None):
        return {"workflow_run": {"id": run_id}, "created_at": date, "expired": expired,
                "name": name or f"build-styles-{run_id}"}

    # Deliberately place the newest usable artifact on the second API page.
    pages = [
        {"artifacts": [artifact(10, "2026-10-10T00:00:00Z"),
                       artifact(30, "2026-10-30T00:00:00Z", expired=True)]},
        {"artifacts": [artifact(20, "2026-10-20T00:00:00Z"),
                       artifact(40, "2026-10-31T00:00:00Z", name="build-style-summary-40")]},
    ]
    result, candidates = run_listing(tmp_path, pages)
    assert result.returncode == 0, result.stderr
    assert candidates == "20\tbuild-styles-20\n10\tbuild-styles-10\n"


def test_empty_repository_has_no_restore_candidates(tmp_path):
    result, candidates = run_listing(tmp_path, [{"artifacts": []}])
    assert result.returncode == 0, result.stderr
    assert candidates == ""


def test_api_failure_is_not_hidden_by_jq_success(tmp_path):
    result, candidates = run_listing(tmp_path, [], api_failure=True)
    assert result.returncode == 7
    assert candidates == ""
