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


@pytest.mark.parametrize("status,preferred,allow,branch,event,accepted", [
    ("completed", "100", "false", "main", "workflow_dispatch", True),
    ("in_progress", "100", "true", "main", "workflow_dispatch", True),
    ("in_progress", "100", "false", "main", "workflow_dispatch", False),
    ("in_progress", "200", "true", "main", "workflow_dispatch", False),
    ("in_progress", "100", "true", "feature", "workflow_dispatch", False),
    ("in_progress", "100", "true", "main", "pull_request_target", False),
])
def test_only_the_verified_preferred_upload_may_be_restored_before_workflow_completion(status, preferred, allow, branch, event, accepted):
    script = textwrap.dedent(ACTION.read_text().split("      run: |\n", 1)[1])
    block = script[script.index('uploaded_preferred="false"'):script.index('staging="$(mktemp', script.index('uploaded_preferred="false"'))]
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", f"for run_id in 100; do\n{block}\necho accepted\ndone"],
        env={**os.environ, "status": status, "BUILD_STYLE_PREFERRED_RUN": preferred,
             "BUILD_STYLE_ALLOW_UPLOADED_RUN": allow, "source_branch": branch,
             "source_event": event, "BUILD_STYLE_DEFAULT_BRANCH": "main"}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert ("accepted" in result.stdout) is accepted


def run_restore(tmp_path, download_plan):
    """Run the complete composite-action shell against deterministic CLI fakes."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # The production runner supplies `python`; local macOS may only expose
    # python3, so bind the exact interpreter running this test.
    (fake_bin / "python").symlink_to(sys.executable)
    executable = fake_bin / "gh"
    executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
        import json, os, sys
        from pathlib import Path
        args = sys.argv[1:]
        if args and args[0] == "api":
            if "--slurp" in args:
                if "--jq" in args or "--template" in args:
                    sys.exit("Invalid slurp formatter")
                print(json.dumps([{"artifacts": [
                    {"name": "build-styles-100", "workflow_run": {"id": 100}, "expired": False, "created_at": "2026-10-30T10:00:00Z"},
                    {"name": "build-styles-90", "workflow_run": {"id": 90}, "expired": False, "created_at": "2026-10-30T09:00:00Z"}
                ]}]))
            elif "/actions/runs/100/artifacts" in args[1]:
                print("100\\tbuild-styles-100")
            elif args[1] == "repos/owner/repo":
                print("main")
            else:
                sys.exit("Unexpected API route")
        elif args[:2] == ["run", "view"]:
            print("completed\\tmain\\tworkflow_dispatch")
        elif args[:2] == ["run", "download"]:
            run_id = args[2]
            destination = Path(args[args.index("--dir") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            log = Path(os.environ["TEST_DOWNLOAD_LOG"])
            history = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            count = sum(item["run_id"] == run_id for item in history)
            plan = json.loads(os.environ["TEST_DOWNLOAD_PLAN"])
            choices = plan.get(run_id, ["fail"])
            mode = choices[min(count, len(choices) - 1)]
            with log.open("a") as stream:
                stream.write(json.dumps({"run_id": run_id, "directory": str(destination), "mode": mode}) + "\\n")
            (destination / "_collection").mkdir(exist_ok=True)
            if mode == "fail":
                (destination / "stale-from-failed-download.txt").write_text("must never reach the published destination")
                (destination / "_collection/state.json").write_text("partial response")
                sys.exit(7)
            if mode == "invalid":
                (destination / "_collection/state.json").write_text("{")
            else:
                (destination / "_collection/state.json").write_text(json.dumps({"schema_version": 1, "commanders": {}}))
                (destination / "good.json").write_text(json.dumps({"commander_slug": "good", "source_run": run_id}))
        else:
            sys.exit("Unexpected GitHub CLI arguments")
    '''))
    executable.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
        import os, sys
        from pathlib import Path
        with Path(os.environ["TEST_SLEEP_LOG"]).open("a") as stream:
            stream.write(sys.argv[1] + "\\n")
    '''))
    fake_sleep.chmod(0o755)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    destination = tmp_path / "restored"
    destination.mkdir()
    (destination / "keep-seed.txt").write_text("existing seed")
    output = tmp_path / "outputs.txt"
    output.touch()
    download_log, sleep_log = tmp_path / "downloads.jsonl", tmp_path / "sleeps.txt"
    script = textwrap.dedent(ACTION.read_text().split("      run: |\n", 1)[1])
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
        env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
             "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test-no-network-token",
             "BUILD_STYLE_DESTINATION": str(destination), "BUILD_STYLE_PREFERRED_RUN": "100",
             "BUILD_STYLE_ALLOW_UPLOADED_RUN": "false", "RUNNER_TEMP": str(runner_temp),
             "GITHUB_OUTPUT": str(output), "TEST_DOWNLOAD_PLAN": json.dumps(download_plan),
             "TEST_DOWNLOAD_LOG": str(download_log), "TEST_SLEEP_LOG": str(sleep_log)},
        text=True, capture_output=True, check=False)
    downloads = [json.loads(line) for line in download_log.read_text().splitlines()] if download_log.exists() else []
    sleeps = sleep_log.read_text().splitlines() if sleep_log.exists() else []
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return result, destination, downloads, sleeps, outputs


def test_transient_download_retries_same_preferred_artifact_with_fresh_staging(tmp_path):
    result, destination, downloads, sleeps, outputs = run_restore(tmp_path, {"100": ["fail", "valid"], "90": ["valid"]})
    assert result.returncode == 0, result.stderr
    assert [item["run_id"] for item in downloads] == ["100", "100"]
    assert len({item["directory"] for item in downloads}) == 2
    assert sleeps == ["5"]
    assert outputs == {"restored": "true", "run_id": "100"}
    assert json.loads((destination / "good.json").read_text())["source_run"] == "100"
    assert not (destination / "stale-from-failed-download.txt").exists()
    assert all(not Path(item["directory"]).exists() for item in downloads)


def test_persistent_download_failure_has_three_attempts_before_deduplicated_fallback(tmp_path):
    result, destination, downloads, sleeps, outputs = run_restore(tmp_path, {"100": ["fail"], "90": ["valid"]})
    assert result.returncode == 0, result.stderr
    assert [item["run_id"] for item in downloads] == ["100", "100", "100", "90"]
    assert len({item["directory"] for item in downloads}) == 4
    assert sleeps == ["5", "10"]
    assert outputs == {"restored": "true", "run_id": "90"}
    assert json.loads((destination / "good.json").read_text())["source_run"] == "90"
    assert not (destination / "stale-from-failed-download.txt").exists()
    assert all(not Path(item["directory"]).exists() for item in downloads)


def test_downloaded_invalid_json_uses_fallback_without_repeating_the_download(tmp_path):
    result, destination, downloads, sleeps, outputs = run_restore(tmp_path, {"100": ["invalid"], "90": ["valid"]})
    assert result.returncode == 0, result.stderr
    assert [item["run_id"] for item in downloads] == ["100", "90"]
    assert sleeps == []
    assert outputs == {"restored": "true", "run_id": "90"}
    assert json.loads((destination / "good.json").read_text())["source_run"] == "90"
    assert all(not Path(item["directory"]).exists() for item in downloads)


def test_all_downloads_fail_without_contaminating_existing_destination(tmp_path):
    result, destination, downloads, sleeps, outputs = run_restore(tmp_path, {"100": ["fail"], "90": ["fail"]})
    assert result.returncode == 0, result.stderr
    assert [item["run_id"] for item in downloads] == ["100", "100", "100", "90", "90", "90"]
    assert len({item["directory"] for item in downloads}) == 6
    assert sleeps == ["5", "10", "5", "10"]
    assert outputs == {"restored": "false"}
    assert list(destination.iterdir()) == [destination / "keep-seed.txt"]
    assert (destination / "keep-seed.txt").read_text() == "existing seed"
    assert all(not Path(item["directory"]).exists() for item in downloads)
