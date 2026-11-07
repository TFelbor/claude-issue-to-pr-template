"""`.github/claude/report.sh` leaves every implementation run where a person can find it.

These tests run the real script against a real git remote (a bare repository on disk)
with a fake `gh` first on PATH that records every invocation. Asserting on the recorded
argv is asserting on the GitHub calls that would have been made; asserting on the bare
repository is asserting on what was pushed. Needs bash, git and jq, as the runner has.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "claude" / "report.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("git") is None,
    reason="the report script needs git and jq, as the runner has",
)

FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$(printf '%s' "$*" | tr '\n' ' ')" >> "$FAKE_GH_LOG"
# A token the App has already revoked is refused on every route.
if [ "${GH_TOKEN:-}" = "revoked" ]; then
  echo 'HTTP 401: Bad credentials' >&2
  exit 1
fi
case "$1 $2" in
  "api rate_limit") printf '{"rate":{"remaining":4999}}' ;;
  "pr list") printf '%s' "${FAKE_GH_PR_LIST:-[]}" ;;
  "pr create")
    if [ -n "${FAKE_GH_PR_CREATE_FAILS:-}" ]; then
      echo 'GitHub Actions is not permitted to create or approve pull requests' >&2
      exit 1
    fi
    echo "https://github.com/example/repo/pull/99" ;;
  "pr comment"|"issue comment"|"pr ready"|"pr edit") : ;;
  *) echo "fake gh: unexpected invocation: $*" >&2; exit 64 ;;
esac
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> dict:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "config", "user.email", "seed@example.invalid")
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "init", "-q", "--bare", str(origin))
    _git(seed, "push", "-q", str(origin), "main")

    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "claude/issue-7")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    log.touch()
    return {"origin": origin, "work": work, "bin": bin_dir, "log": log, "tmp": tmp_path}


def _run(repo: dict, **env: str) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        "PATH": f"{repo['bin']}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_LOG": str(repo["log"]),
        "GH_TOKEN": "fake",
        "ISSUE_NUMBER": "7",
        "ISSUE_TITLE": "Add a health endpoint",
        "BRANCH": "claude/issue-7",
        "BASE_BRANCH": "main",
        "RUN_URL": "https://github.com/example/repo/actions/runs/1",
        "MODE": "implement",
        # The model's identity is not configured on the runner's checkout.
        "GIT_CONFIG_GLOBAL": str(repo["tmp"] / "no-global-gitconfig"),
        **env,
    }
    return subprocess.run(["bash", str(SCRIPT)], cwd=repo["work"], env=full_env, capture_output=True, text=True)


def _calls(repo: dict) -> list[str]:
    return [line for line in repo["log"].read_text().splitlines() if line]


def _commit(repo: dict, name: str) -> None:
    (repo["work"] / name).write_text(f"{name}\n")
    _git(repo["work"], "add", name)
    _git(repo["work"], "-c", "user.name=Model", "-c", "user.email=model@example.invalid",
         "commit", "-q", "-m", f"feat: {name}")


def _remote_has_branch(repo: dict) -> bool:
    return "claude/issue-7" in _git(repo["origin"], "branch", "--list", "claude/issue-7")


def _execution_file(repo: dict, subtype: str = "error_max_turns", turns: int = 201) -> str:
    path = repo["tmp"] / "execution.json"
    result = {"type": "result", "subtype": subtype, "is_error": subtype != "success",
              "num_turns": turns, "permission_denials": []}
    path.write_text(json.dumps([{"type": "system", "subtype": "init"}, result]))
    return str(path)


DRAFT_FIRST = json.dumps([{
    "number": 12, "url": "https://github.com/example/repo/pull/12", "isDraft": True,
    "title": "WIP: Add a health endpoint",
    "body": "Closes #7\n\n<!-- claude-implement-draft-first -->\n- [x] the endpoint answers",
}])


def test_failure_with_nothing_committed_tells_the_issue(repo):
    result = _run(repo, CONCLUSION="failure", EXECUTION_FILE=_execution_file(repo))
    assert result.returncode == 0, result.stderr
    (comment,) = [c for c in _calls(repo) if c.startswith("issue comment 7 ")]
    assert "error_max_turns after 201 turns" in comment
    assert not _remote_has_branch(repo)


def test_uncommitted_edits_are_committed_pushed_and_opened_as_a_draft(repo):
    (repo["work"] / "half_done.py").write_text("x = 1\n")
    result = _run(repo, CONCLUSION="failure", EXECUTION_FILE=_execution_file(repo, "error_during_execution", 12))
    assert result.returncode == 0, result.stderr
    assert _remote_has_branch(repo)
    assert "WIP:" in _git(repo["origin"], "log", "-1", "--format=%s", "claude/issue-7")
    (create,) = [c for c in _calls(repo) if c.startswith("pr create ")]
    assert "--draft" in create
    assert "<!-- claude-implement-safety-net subtype=error_during_execution -->" in create


def test_a_revoked_app_token_falls_back_to_the_workflow_token(repo):
    _commit(repo, "a.py")
    result = _run(repo, GH_TOKEN="revoked", GH_TOKEN_FALLBACK="workflow",
                  CONCLUSION="failure", EXECUTION_FILE=_execution_file(repo))
    assert result.returncode == 0, result.stderr
    assert any(c.startswith("pr create ") for c in _calls(repo))
    assert "reporting as the workflow instead" in result.stderr


def test_a_refused_pr_create_says_so_on_the_issue(repo):
    _commit(repo, "a.py")
    _run(repo, CONCLUSION="failure", EXECUTION_FILE=_execution_file(repo), FAKE_GH_PR_CREATE_FAILS="1")
    (comment,) = [c for c in _calls(repo) if c.startswith("issue comment 7 ")]
    assert "no pull request could be opened" in comment
    assert "The draft should already have existed" in comment


def test_success_on_a_ticked_draft_first_draft_marks_it_ready(repo):
    _commit(repo, "a.py")
    _run(repo, CONCLUSION="success", EXECUTION_FILE=_execution_file(repo, "success", 40), FAKE_GH_PR_LIST=DRAFT_FIRST)
    calls = _calls(repo)
    assert "pr ready 12" in calls
    assert "pr edit 12 --title Add a health endpoint" in calls
    (comment,) = [c for c in calls if c.startswith("pr comment 12 ")]
    assert "<!-- claude-implement-finished -->" in comment


def test_success_with_unticked_criteria_stays_a_draft(repo):
    _commit(repo, "a.py")
    unticked = DRAFT_FIRST.replace("- [x]", "- [ ]")
    _run(repo, CONCLUSION="success", EXECUTION_FILE=_execution_file(repo, "success", 40), FAKE_GH_PR_LIST=unticked)
    calls = _calls(repo)
    assert "pr ready 12" not in calls
    (comment,) = [c for c in calls if c.startswith("pr comment 12 ")]
    assert "still unticked" in comment


def test_a_cancelled_run_is_recorded_as_cancelled_not_retryable(repo):
    _commit(repo, "a.py")
    _run(repo, CONCLUSION="cancelled", FAKE_GH_PR_LIST=DRAFT_FIRST)
    (comment,) = [c for c in _calls(repo) if c.startswith("pr comment 12 ")]
    assert "subtype=cancelled" in comment


def test_the_script_exits_zero_even_when_github_is_unreachable(repo):
    _commit(repo, "a.py")
    (repo["bin"] / "gh").write_text("#!/usr/bin/env bash\necho 'HTTP 502' >&2\nexit 1\n")
    result = _run(repo, CONCLUSION="failure", EXECUTION_FILE=_execution_file(repo))
    assert result.returncode == 0, result.stderr
