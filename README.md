# Claude issue-to-PR pipeline template

Five GitHub Actions workflows that turn a labelled issue into a reviewed pull request
with Claude Code, and keep going when things fail:

- **plan** (optional) — a planning model posts a written plan on the issue first;
- **implement** — the implementer opens a draft pull request on its first commit,
  implements, runs your tests, ticks the acceptance criteria and marks it ready;
- **review** — a *different* model reviews it and turns the verdict into a status check
  that is red when it requests changes, then asks the implementer to fix it (three rounds
  at most);
- **resume** — drafts cut short by a usage limit are picked up again, three tries at most,
  and never after a person has commented;
- **auto-merge** — off unless you turn it on, and gated eight ways when you do.

Every run, however it ends, leaves its work on a branch with a pull request and says on
the issue what happened. Most of the code is there because, at some point, a run failed
and nothing said so. `docs/PIPELINE.md` is the operator's guide, including a list of the
failure modes that shaped it.

## Quick start

1. Create a repository from this template, or copy `.github/`, `docs/PIPELINE.md`,
   `tests/test_claude_pipeline_*.py`, `CLAUDE.md.example` and the `.gitignore` entry
   into an existing one.
2. In Claude Code, run `/install-github-app` for the repository. It installs the Claude
   GitHub App and creates the `CLAUDE_CODE_OAUTH_TOKEN` secret, the only one used.
3. Create the seven labels (`claude`, `tier:fable-plan`, `tier:opus-direct`,
   `plan-ready`, `risk:review`, `risk:auto`, `do-not-merge`); the `gh label create`
   commands are in `docs/PIPELINE.md` § Setup.
4. Copy `CLAUDE.md.example` to `CLAUDE.md` and fill in the placeholders. Keep the lines
   marked *(keep)*.
5. Set the repository variables your project needs: `SETUP_COMMAND`, `TEST_COMMAND`,
   and `PYTHON_VERSION` / `NODE_VERSION` if you use those toolchains.
6. Edit the `# >>> CUSTOMISE` blocks in `.github/workflows/claude-implement.yml`: the
   `--allowedTools` list must cover your setup and test commands, and add a `services:`
   block if your tests need a database.
7. Push. `template-selftest` should go green: it checks the pipeline's own invariants
   and needs no secrets.
8. Open a small issue with a clear acceptance-criteria checklist. Add
   `tier:opus-direct` and `risk:review`, then add **`claude` last** — it is the switch.
9. Watch the run: a draft pull request appears within a few minutes, becomes ready, and
   `claude-review` reviews it. After that first review, require the `claude-review`
   check in branch protection.
10. Read the pull request and merge it yourself. Leave `AUTO_MERGE` unset.

## How a run looks

```
00:00  you add `claude` to issue #12 (already tier:opus-direct, risk:review)
00:01  claude-implement starts; installs the project; creates claude/issue-12
00:04  first commit pushed; draft PR opened with the criteria as an unticked checklist
00:04  ... implements, commits and pushes in small steps, runs the tests ...
00:25  every box ticked, full test command green; PR marked ready
00:26  claude-review runs on `ready_for_review`
00:31  verdict request_changes -> check red, "@claude please address the review (1 of 3)"
00:32  the fix job pushes to the same branch -> claude-review runs again
00:40  verdict approve -> check green, one sticky status comment updated in place
       a human reads the diff and merges
```

If the implementer runs out of budget at 00:20, the work so far is still on the draft,
the draft says how the run ended, and the issue is told. If a usage limit cut it short,
`claude-resume` dispatches it again within about six hours.

## What it costs, roughly

Illustrative figures from one small team's use, on Claude subscription tokens (which
count against usage limits rather than billing per token; the action reports an
API-equivalent figure as `total_cost_usd` in each run's log):

- a review: about USD 3–4 of model time and 3–6 minutes;
- a small implementation (a README section): about 30 turns, about USD 3;
- a large issue that should have been planned first: 80+ turns and USD 10 before it ran
  out of budget with nothing to show. Tier your issues;
- GitHub Actions minutes: an implement run can take most of its 90-minute timeout; a
  private repository on GitHub Free includes 2 000 Linux minutes a month, and Linux
  overage is under a cent a minute. The six-hourly resume cron costs at least a minute a
  tick even when it finds nothing.

Every push to a ready pull request is a new review. Iterate in draft.

## Honest limits

- **This is an unattended agent with write access to your repository.** It edits code,
  runs your build tools and pushes branches on the strength of an issue and a label.
  Keep a human merge. Auto-merge exists, is off by default, and should stay off until
  you have watched many runs.
- The trust boundary is a set of narrow gates, not a sandbox. Prompt injection through an
  issue that quotes untrusted text, or through a diff the reviewer reads, is bounded (the
  `@claude` trigger only answers owners, members and collaborators; the reviewer cannot
  write code; the review→fix hop is capped and defanged) but not eliminated. Read
  `docs/PIPELINE.md` § The trust boundary before opening the repository to outside
  contributors.
- Pull requests from forks are not reviewed (no secrets on fork runs) and are refused by
  the fix loop.
- A pull request that edits `.github/workflows/` is never machine-reviewed: the action
  refuses to run, and the check passes saying a human must read it.
- On GitHub Free, private repositories have no branch protection, so nothing can
  *require* the review check.
- The model ids are defaults from late 2026; set `CLAUDE_*_MODEL` to what you have.
- The pipeline has been run for real on one private repository, by a small team, for a
  few months. The workflows are tested statically and the scripts against a fake `gh`;
  your first live runs are still the real test.

## Repository layout

```
.github/workflows/claude-{plan,implement,review,resume,automerge}.yml
.github/workflows/template-selftest.yml   static tests, no secrets
.github/actionlint.yaml                   actionlint config (one documented ignore)
.github/claude/report.sh                  the always() tail of every implement/fix run
.github/claude/resume.py                  the resume rules
.github/claude/setup-project.sh           default project setup (or SETUP_COMMAND)
CLAUDE.md.example                         the contract template
docs/PIPELINE.md                          the operator's guide
tests/test_claude_pipeline_*.py           pytest, stdlib + PyYAML
```

Run the tests locally with `python -m pip install -r .github/claude/requirements-selftest.txt`
and `python -m pytest -q tests/test_claude_pipeline_*.py` (the report tests also need
`git` and `jq`).

## Origin and licence

Extracted from Daedal Studio (2026), a closed startup whose full codebase is archived at https://github.com/TFelbor/daedal-studio; see the write-up at https://substack.com/@tytusfelbor.

Apache-2.0; see `LICENSE` and `NOTICE`.
