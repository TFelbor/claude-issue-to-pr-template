"""`.github/claude/resume.py` re-dispatches only what a retry can help, and stops.

Every rule lives on the pure `decide()` function, so each is asserted here without a
network or a `gh`. Stdlib only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "claude" / "resume.py"
REPORT = REPO_ROOT / ".github" / "claude" / "report.sh"
IMPLEMENT = REPO_ROOT / ".github" / "workflows" / "claude-implement.yml"
CONTRACT = REPO_ROOT / "CLAUDE.md.example"

spec = importlib.util.spec_from_file_location("claude_pipeline_resume", SCRIPT)
resume = importlib.util.module_from_spec(spec)
# `dataclass` resolves the class's module through sys.modules.
sys.modules["claude_pipeline_resume"] = resume
spec.loader.exec_module(resume)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
MARK = "<!-- claude-implement-safety-net subtype={} -->"


def _pr(draft=True, subtype="error_during_execution", branch="claude/issue-7", created=NOW - timedelta(hours=5)):
    body = f"Closes #7\n\n{MARK.format(subtype)}\nOpened by the workflow" if subtype else "Closes #7\n\nmy own draft"
    return {"number": 12, "isDraft": draft, "body": body, "headRefName": branch, "createdAt": created.isoformat()}


def _draft_first_pr():
    body = "Closes #7\n\n<!-- claude-implement-draft-first -->\n- [ ] the first one\n"
    return {"number": 12, "isDraft": True, "body": body, "headRefName": "claude/issue-7",
            "createdAt": (NOW - timedelta(hours=5)).isoformat()}


def _comment(author, body, when):
    return {"author": {"login": author}, "body": body, "createdAt": when.isoformat()}


def _resume_comment(k, when):
    return _comment("github-actions", f"<!-- claude-implement-resume --> Resume attempt {k} of 3.", when)


# --- what is not the pipeline's to touch --------------------------------------------------


def test_a_ready_pull_request_is_never_redispatched():
    d = resume.decide(_pr(draft=False), [], NOW)
    assert d.action == "skip" and "draft" in d.reason


def test_a_draft_without_a_marker_is_left_alone():
    d = resume.decide(_pr(subtype=None), [], NOW)
    assert d.action == "skip" and "no marker" in d.reason


def test_a_finished_run_stops_the_loop():
    comments = [_comment("claude", "<!-- claude-implement-finished --> done", NOW - timedelta(hours=1))]
    assert resume.decide(_pr(), comments, NOW).action == "skip"


def test_a_branch_that_is_not_an_issue_branch_is_skipped():
    d = resume.decide(_pr(branch="feature/x"), [], NOW)
    assert d.action == "skip" and "branch" in d.reason


# --- only a transient failure is retried --------------------------------------------------


def test_a_budget_failure_is_not_retried():
    d = resume.decide(_pr(subtype="error_max_turns"), [], NOW)
    assert d.action == "skip" and "error_max_turns" in d.reason


def test_a_cancelled_run_is_not_resumed():
    """docs/PIPELINE.md § How to stop it promises cancelling stops it; this is the skip
    that keeps the promise, because the tail still runs and still records the draft."""
    d = resume.decide(_pr(subtype="cancelled"), [], NOW)
    assert d.action == "skip" and "cancelled" in d.reason


def test_only_the_listed_subtypes_are_retried():
    assert resume.RETRYABLE_SUBTYPES == {"error_during_execution", "none"}
    for unanticipated in ("success", "error_deadline_exceeded", "refusal"):
        assert resume.decide(_pr(subtype=unanticipated), [], NOW).action == "skip"


def test_the_two_scripts_agree_on_which_subtypes_are_retried():
    """report.sh tells the reader whether a retry is coming; resume.py decides it."""
    declared = re.search(r'^RETRYABLE_SUBTYPES="([^"]*)"', REPORT.read_text(), re.MULTILINE)
    assert declared, "report.sh no longer declares RETRYABLE_SUBTYPES"
    assert set(declared.group(1).split()) == set(resume.RETRYABLE_SUBTYPES)


# --- the draft the model opens first ------------------------------------------------------


def test_a_draft_first_draft_with_no_outcome_yet_is_left_alone():
    """The marker means "a run is working here". Dispatching over it would put a second
    run on a branch whose first run is still going."""
    d = resume.decide(_draft_first_pr(), [], NOW)
    assert d.action == "skip" and "not reported an outcome" in d.reason


def test_a_draft_first_draft_is_resumed_once_a_transient_failure_is_recorded():
    comments = [_comment("claude", MARK.format("error_during_execution"), NOW - timedelta(hours=3))]
    d = resume.decide(_draft_first_pr(), comments, NOW)
    assert d.action == "dispatch" and d.issue == 7


def test_every_place_that_reads_the_draft_first_marker_agrees_on_it():
    marker = resume.DRAFT_FIRST_MARKER
    assert f"DRAFT_FIRST_MARKER='{marker}'" in REPORT.read_text()
    assert f"{marker} -->" in IMPLEMENT.read_text(), "the implement prompt must ask for the marker"
    assert f"{marker} -->" in CONTRACT.read_text(), "CLAUDE.md.example must carry the convention"


def test_the_guard_pattern_matches_both_pipeline_markers():
    """A draft the guard does not recognise as the pipeline's blocks its own issue."""
    ours = re.search(r"^\s*ours='([^']*)'", IMPLEMENT.read_text(), re.MULTILINE)
    assert ours, "claude-implement.yml's guard has no ours='...' pattern"
    for marker in (resume.DRAFT_FIRST_MARKER, resume.SAFETY_NET_MARKER):
        assert re.search(ours.group(1), marker)


# --- a person takes over at their first comment -------------------------------------------


def test_a_human_comment_after_the_last_record_means_a_person_took_over():
    comments = [_comment("a-maintainer", "I'll finish this by hand.", NOW - timedelta(hours=1))]
    d = resume.decide(_pr(), comments, NOW)
    assert d.action == "skip" and "person" in d.reason


def test_every_spelling_of_the_bots_is_not_a_person():
    comments = [
        _comment("claude", "review", NOW - timedelta(hours=2)),
        _comment("github-actions", "<!-- claude-fix-request --> @claude", NOW - timedelta(hours=2)),
        _comment("claude[bot]", "noted", NOW - timedelta(hours=1)),
        _comment("app/claude", "as gh spells a PR author", NOW - timedelta(minutes=30)),
    ]
    assert resume.decide(_pr(), comments, NOW).action == "dispatch"


def test_a_person_who_comments_during_an_attempt_is_not_talked_over():
    comments = [
        _resume_comment(1, NOW - timedelta(hours=4)),
        _comment("a-maintainer", "hold off", NOW - timedelta(hours=3, minutes=30)),
        _comment("claude", MARK.format("error_during_execution"), NOW - timedelta(hours=3)),
    ]
    d = resume.decide(_pr(), comments, NOW)
    assert d.action == "skip" and "a-maintainer" in d.reason


# --- the cap and the gap ------------------------------------------------------------------


def test_the_first_dispatch_is_attempt_one():
    d = resume.decide(_pr(), [], NOW)
    assert d.action == "dispatch" and d.attempt == 1 and d.issue == 7


def test_attempts_stop_at_three():
    assert resume.DEFAULT_MAX_ATTEMPTS == 3
    comments = [_resume_comment(k, NOW - timedelta(hours=10 - 2 * k)) for k in (1, 2, 3)]
    d = resume.decide(_pr(), comments, NOW)
    assert d.action == "skip" and "3" in d.reason


def test_a_recent_attempt_is_given_time_to_finish():
    comments = [_resume_comment(1, NOW - timedelta(minutes=40))]
    d = resume.decide(_pr(), comments, NOW, min_gap=timedelta(hours=2))
    assert d.action == "skip" and "40 minutes" in d.reason
