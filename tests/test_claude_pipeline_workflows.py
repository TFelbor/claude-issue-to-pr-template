"""The workflows keep the properties the pipeline's safety rests on.

Every property here failed silently at least once in the repository this template was
extracted from: nothing went red, a run simply did nothing, or did the wrong thing, and
somebody found out days later. So they are asserted on the parsed YAML, which is free
to *mention* anything in a comment, rather than left to a reviewer's memory.

Stdlib + PyYAML only. docs/PIPELINE.md explains each one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PLAN = WORKFLOWS / "claude-plan.yml"
IMPLEMENT = WORKFLOWS / "claude-implement.yml"
REVIEW = WORKFLOWS / "claude-review.yml"
RESUME = WORKFLOWS / "claude-resume.yml"
AUTOMERGE = WORKFLOWS / "claude-automerge.yml"
PIPELINE = (PLAN, IMPLEMENT, REVIEW, RESUME, AUTOMERGE)
ALL_WORKFLOWS = sorted(WORKFLOWS.glob("*.yml"))

ACTION = "anthropics/claude-code-action"
PINNED = re.compile(rf"^{re.escape(ACTION)}@v\d+\.\d+\.\d+$")
DRAFT_FIRST = "<!-- claude-implement-draft-first -->"
FIX_MARKER = "<!-- claude-fix-request -->"
STATUS_MARKER = "<!-- claude-review-status -->"
APP_SPELLINGS = {"claude", "claude[bot]", "app/claude"}
#: Seconds a turn at the slowest pace measured, used to check that a job's timeout can
#: pay for its own turn budget. A budget the timeout cannot pay for becomes a timeout,
#: and a timeout kills the run before the transcript and the report are kept.
SECONDS_PER_TURN = 13


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} did not parse to a mapping"
    return data


def _triggers(workflow: dict) -> dict:
    # PyYAML reads a bare `on:` key as the boolean True (YAML 1.1).
    return workflow.get("on", workflow.get(True))


def _job(path: Path, name: str) -> dict:
    jobs = _load(path)["jobs"]
    assert name in jobs, f"{path.name} has no job {name!r}"
    return jobs[name]


def _step(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _action_steps(job: dict) -> list[dict]:
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith(ACTION)]


def _claude_args(step: dict) -> str:
    return step.get("with", {}).get("claude_args", "")


def _max_turns(step: dict) -> int:
    match = re.search(r"--max-turns\s+(\d+)", _claude_args(step))
    assert match, f"step {step.get('name')!r} sets no literal --max-turns"
    return int(match.group(1))


def _allowed_tools(step: dict) -> list[str]:
    match = re.search(r'--allowedTools\s+"([^"]*)"', _claude_args(step))
    assert match, f"step {step.get('name')!r} sets no --allowedTools"
    return [t.strip() for t in match.group(1).split(",")]


def _all_jobs():
    for path in PIPELINE:
        for name, job in _load(path)["jobs"].items():
            yield path, name, job


def _model_jobs():
    for path, name, job in _all_jobs():
        for step in _action_steps(job):
            yield pytest.param(path, name, job, step, id=f"{path.stem}:{name}")


# --- every workflow parses, and every job is bounded ------------------------------------


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_parses(path):
    workflow = _load(path)
    assert _triggers(workflow), f"{path.name} has no triggers"
    assert workflow.get("jobs"), f"{path.name} has no jobs"


@pytest.mark.parametrize("path,name,job", [pytest.param(p, n, j, id=f"{p.stem}:{n}") for p, n, j in _all_jobs()])
def test_every_job_has_a_timeout(path, name, job):
    """`timeout-minutes` is the real cost bound. GitHub's default is six hours."""
    timeout = job.get("timeout-minutes")
    assert isinstance(timeout, int) and 0 < timeout <= 120, f"{path.name}:{name} timeout is {timeout!r}"


@pytest.mark.parametrize("path,name,job,step", list(_model_jobs()))
def test_the_turn_budget_fits_inside_the_timeout(path, name, job, step):
    """`--max-turns` is a budget assertion, not a limiter; the timeout must pay for it."""
    minutes_needed = _max_turns(step) * SECONDS_PER_TURN / 60
    # The review's budget is deliberately generous slack over ~32 real turns; only the
    # planner and implementer are sized to their measured paces.
    if name == "claude-review":
        return
    assert minutes_needed <= job["timeout-minutes"], (
        f"{path.name}:{name}: {_max_turns(step)} turns need ~{minutes_needed:.0f} min "
        f"but the job times out at {job['timeout-minutes']}"
    )


# --- one credential, one pinned action --------------------------------------------------


def test_the_api_key_is_never_named_anywhere():
    """An unset secret renders as an empty string, which the action maps straight into
    the model's environment: naming a secret that does not exist sets an *empty*
    credential. The name is assembled here so this file does not contain it either."""
    forbidden = ("anthropic" + "_api_key").lower()
    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore").lower()
        except OSError:
            continue
        if forbidden in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"{offenders} name the API-key credential"


@pytest.mark.parametrize("path", PIPELINE, ids=lambda p: p.name)
def test_the_oauth_token_is_the_only_secret(path):
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", path.read_text()))
    assert secrets <= {"CLAUDE_CODE_OAUTH_TOKEN"}, f"{path.name} references {secrets}"


@pytest.mark.parametrize("path,name,job,step", list(_model_jobs()))
def test_every_model_step_authenticates_with_the_oauth_token(path, name, job, step):
    assert step["with"].get("claude_code_oauth_token") == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"


def test_every_action_step_pins_the_same_exact_release():
    """`@v1` is a moving tag: the model's tools and auth handling could change under a
    workflow nobody edited."""
    uses = {s["uses"] for _, _, job in _all_jobs() for s in _action_steps(job)}
    assert uses, "no claude-code-action step found"
    assert len(uses) == 1, f"different pins: {uses}"
    assert PINNED.match(uses.pop())


@pytest.mark.parametrize("path,name,job,step", list(_model_jobs()))
def test_the_model_is_a_variable_with_a_default(path, name, job, step):
    assert re.search(r"--model \$\{\{ vars\.CLAUDE_[A-Z]+_MODEL \|\| '[a-z0-9.-]+' \}\}", _claude_args(step))


@pytest.mark.parametrize("path,name,job,step", list(_model_jobs()))
def test_no_job_admits_every_bot(path, name, job, step):
    """An allowed bot also skips the write-permission check, so `*` lets any App that can
    open a pull request choose the prompt."""
    bots = step["with"].get("allowed_bots", "")
    assert "*" not in bots


# --- the claude label is the release switch ---------------------------------------------


def test_plan_needs_claude_and_the_plan_tier_and_not_plan_ready():
    condition = _job(PLAN, "plan")["if"]
    assert "'claude'" in condition and "'tier:fable-plan'" in condition
    assert "!contains(github.event.issue.labels.*.name, 'plan-ready')" in condition


def test_implement_needs_claude_and_a_tier():
    condition = _job(IMPLEMENT, "implement")["if"]
    assert "'claude'" in condition
    assert "'plan-ready'" in condition and "'tier:opus-direct'" in condition


def test_a_dispatched_run_still_checks_the_claude_label():
    run = _step(_job(IMPLEMENT, "implement"), "Resolve the issue")["run"]
    assert "grep -qx 'claude'" in run


def test_implement_admits_the_app_that_adds_plan_ready():
    step = _step(_job(IMPLEMENT, "implement"), "Implement")
    bots = {b.strip() for b in step["with"]["allowed_bots"].split(",")}
    assert {"claude[bot]", "github-actions[bot]"} <= bots


# --- the draft comes first, and every run reports back ----------------------------------


def test_the_implementer_opens_a_marked_draft_first():
    prompt = _step(_job(IMPLEMENT, "implement"), "Implement")["with"]["prompt"]
    assert DRAFT_FIRST in prompt
    assert "gh pr create --draft" in prompt
    assert "first commit" in prompt


@pytest.mark.parametrize("job_name,step_name", [("implement", "Implement"), ("fix", "Address the comment")])
def test_skill_is_on_the_allowlist(job_name, step_name):
    assert "Skill" in _allowed_tools(_step(_job(IMPLEMENT, job_name), step_name))


def test_review_can_invoke_skills_and_cannot_write():
    tools = _allowed_tools(_step(_job(REVIEW, "claude-review"), "Review"))
    assert "Skill" in tools
    assert not {"Edit", "MultiEdit", "Write"} & set(tools)
    assert not any(t.startswith("Bash(git") for t in tools)


@pytest.mark.parametrize("job_name", ["implement", "fix"])
def test_the_report_tail_runs_whatever_happened(job_name):
    job = _job(IMPLEMENT, job_name)
    tail = _step(job, "Leave the work where a person can find it")
    assert tail["if"].startswith("always()")
    assert tail["env"]["GH_TOKEN_FALLBACK"] == "${{ github.token }}"
    # The default branch's copy, stashed before the model ran, not the checkout's.
    assert '"$RUNNER_TEMP/claude-pipeline/report.sh"' in tail["run"]
    assert "cp .github/claude/" in _step(job, "Stash the pipeline's own scripts")["run"]
    transcript = _step(job, "Keep the transcript")
    assert transcript["if"].startswith("always()")


def test_the_branch_is_created_by_the_workflow():
    """The action ignores `branch_name_template` in automation mode."""
    job = _job(IMPLEMENT, "implement")
    assert "git checkout -q -b" in _step(job, "Create or resume the working branch")["run"]
    assert "branch_name_template" not in str(_step(job, "Implement")["with"])


def test_a_failed_plan_reports_to_the_issue_only_when_it_did_not_land():
    job = _job(PLAN, "plan")
    assert _step(job, "Did the plan land?")["if"] == "failure()"
    report = _step(job, "Report a failed plan to the issue")
    assert report["if"] == "failure() && steps.landed.outputs.landed != 'true'"
    assert "gh issue comment" in report["run"]


# --- the fix loop's trust boundary -----------------------------------------------------


def test_the_at_claude_trigger_checks_the_author_association():
    """With no `prompt`, the comment *is* the instruction, and behind it is `Write` and a
    package installer. The source repository left this check out because it was private;
    a template must not."""
    condition = _job(IMPLEMENT, "fix")["if"]
    assert "github.event.comment.author_association" in condition
    for association in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert f'"{association}"' in condition
    for too_wide in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"):
        assert too_wide not in condition


def test_the_only_bot_the_fix_trigger_admits_is_the_review_hop():
    condition = _job(IMPLEMENT, "fix")["if"]
    assert "github.event.comment.user.login == 'github-actions[bot]'" in condition
    assert FIX_MARKER in condition, "the bot clause must require the fix-request marker"
    assert "claude[bot]" not in condition, "a review the model wrote must not summon the implementer"
    step = _step(_job(IMPLEMENT, "fix"), "Address the comment")
    assert step["with"]["allowed_bots"] == "github-actions[bot]"


def test_the_fix_job_refuses_closed_merged_and_issue_threads():
    condition = _job(IMPLEMENT, "fix")["if"]
    assert "github.event.issue.pull_request" in condition
    assert "github.event.issue.state == 'open'" in condition
    assert "merged_at == null" in condition


def test_the_fix_job_refuses_forks_and_skips_its_tail_when_it_does():
    job = _job(IMPLEMENT, "fix")
    assert "isCrossRepository" in _step(job, "Refuse pull requests from forks")["run"]
    assert "steps.pr.outputs.ok == 'true'" in _step(job, "Leave the work where a person can find it")["if"]


# --- the review check -------------------------------------------------------------------


def test_review_skips_drafts_and_runs_when_they_are_ready():
    assert _job(REVIEW, "claude-review")["if"] == "github.event.pull_request.draft == false"
    assert "ready_for_review" in _triggers(_load(REVIEW))["pull_request"]["types"]


def test_the_fix_loop_is_capped_at_three_rounds():
    decide = _step(_job(REVIEW, "claude-review"), "Decide the check from the verdict")
    assert decide["env"]["MAX_ROUNDS"] == "3"
    assert decide["if"] == "always()"


def test_the_status_comment_covers_every_outcome_and_is_not_the_fix_marker():
    run = _step(_job(REVIEW, "claude-review"), "Decide the check from the verdict")["run"]
    assert f"STATUS_MARKER='{STATUS_MARKER}'" in run
    assert STATUS_MARKER != FIX_MARKER
    # exemption, no verdict, approve, request_changes withheld, request_changes asked
    assert run.count('upsert_status "') >= 5
    # Posting the status must never change the check's colour.
    assert run.count("|| echo \"::warning::could not") == 2


def test_the_review_summary_cannot_mention_anyone():
    """The summary is model text quoted into a comment the fix job may obey."""
    run = _step(_job(REVIEW, "claude-review"), "Decide the check from the verdict")["run"]
    assert "sed 's/@/(at)/g'" in run


def test_review_and_automerge_agree_on_every_spelling_of_the_app():
    review = _step(_job(REVIEW, "claude-review"), "Decide the check from the verdict")["env"]["CLAUDE_LOGINS"]
    automerge = _step(_job(AUTOMERGE, "automerge"), "Evaluate the gate and merge")["env"]["CLAUDE_LOGINS"]
    assert review == automerge
    assert APP_SPELLINGS <= set(review.split())
    for path in (REVIEW, AUTOMERGE):
        assert r"s#^app/##; s#\[bot\]$##" in path.read_text(), f"{path.name} does not normalise the login"


def test_review_full_output_is_a_lever_that_defaults_off():
    step = _step(_job(REVIEW, "claude-review"), "Review")
    assert step["with"]["show_full_output"] == "${{ vars.CLAUDE_REVIEW_VERBOSE == 'on' }}"


# --- resume and auto-merge --------------------------------------------------------------


def test_resume_is_capped_at_three_attempts():
    step = _step(_job(RESUME, "resume"), "Re-dispatch unfinished implementations")
    assert step["env"]["MAX_ATTEMPTS"] == "3"
    assert step["run"] == "python3 .github/claude/resume.py"


def test_auto_merge_is_off_unless_the_variable_says_on():
    """An unset variable is the empty string, which is not 'on'. No fallback may turn it on."""
    assert _job(AUTOMERGE, "automerge")["if"] == "vars.AUTO_MERGE == 'on'"
    text = AUTOMERGE.read_text()
    assert "vars.AUTO_MERGE ||" not in text and "vars.AUTO_MERGE !=" not in text


def test_auto_merge_requires_every_gate():
    run = _step(_job(AUTOMERGE, "automerge"), "Evaluate the gate and merge")["run"]
    for gate in ('" risk:auto "', '" do-not-merge "', '"MERGEABLE"', '"claude-review"',
                 "isCrossRepository", "PROTECTED_PATHS", "--match-head-commit"):
        assert gate in run, f"auto-merge lost the {gate} gate"
    env = _step(_job(AUTOMERGE, "automerge"), "Evaluate the gate and merge")["env"]
    assert env["PROTECTED_PATHS"].startswith(".github/ ")
