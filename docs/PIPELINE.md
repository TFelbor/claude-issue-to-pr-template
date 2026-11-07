# The Claude Code pipeline

How a labelled issue becomes a reviewed pull request, what each label means, how to set
it up, and how to stop it. Read § The trust boundary before you change any trigger.

## The path

```
issue + labels  (add `claude` LAST: it is the release switch)
      │
      ├─ tier:fable-plan ──▶ claude-plan.yml (planner model)
      │                        posts a "Plan" comment, adds `plan-ready`
      │                                    │
      └─ tier:opus-direct ─────────────────┤
                                           ▼
                              claude-implement.yml · job `implement` (implementer model)
                                branch claude/issue-<n>, draft PR opened first
                                (§ The draft comes first), tests run, PR marked ready
                                — or, when the run ends early: work pushed, the draft
                                  left where it is, issue told why (§ When a run fails)
                                           │
                                           ▼
                              claude-review.yml (reviewer model, a different one)
                                status check `claude-review`
                                  ├─ approve ─────────▶ green
                                  └─ request changes ─▶ red + "@claude please
                                                        address the review"
                                                              │   (max 3 rounds)
                                           claude-implement.yml · job `fix`
                                             pushes to the same branch ─┐
                                                                        │
                                           (back to claude-review) ◀─────┘
                                           │
                                           ▼
                              a human merges  ·  or, only once AUTO_MERGE is `on`,
                                                 claude-automerge.yml

claude-resume.yml (every 6 h): re-dispatches drafts a transient failure cut short,
                               max 3 attempts, stops at the first human comment.
```

Nothing in this pipeline merges anything while `AUTO_MERGE` is unset or `off`, which is
how it starts. Start there, and stay there until you have watched it for a while.

## The workflows

| File | Model | Trigger | What it does |
|---|---|---|---|
| `claude-plan.yml` | `CLAUDE_PLAN_MODEL` (default `claude-fable-5-1`), 150 turns, 45 min | issue labelled | Posts one **Plan** comment, then adds `plan-ready`. Writes no code. When the plan did not land, says so on the issue. |
| `claude-implement.yml` · `implement` | `CLAUDE_IMPLEMENT_MODEL` (default `claude-opus-5`), 200 turns, 90 min | issue labelled, or `workflow_dispatch` with an issue number | Sets up the project, creates `claude/issue-<n>`, **opens a draft pull request before it implements anything**, implements, runs the tests, marks the pull request ready with the acceptance criteria ticked. Whatever happens, keeps the transcript and leaves the work findable. |
| `claude-implement.yml` · `fix` | same as `implement` | `@claude` comment on an **open, unmerged, same-repository** pull request, by an `OWNER`/`MEMBER`/`COLLABORATOR` or by the review workflow's fix request | The fix loop. Interactive mode: no `prompt` input, so the comment *is* the request. Same tail as `implement`. |
| `claude-review.yml` | `CLAUDE_REVIEW_MODEL` (default `claude-fable-5-1`), 80 turns, 20 min | PR opened / synchronised / reopened / marked ready; **drafts skipped** | Reviews against the linked issue and `CLAUDE.md`, and **fails the check** when it asks for changes. One sticky status comment on every outcome. |
| `claude-automerge.yml` | none — plain `gh` | review workflow completed, review submitted, check suite completed | Only when `AUTO_MERGE` is `on`. Squash-merges when every gate passes. |
| `claude-resume.yml` | none — `.github/claude/resume.py` | every six hours, or by hand | Re-dispatches `claude-implement` for drafts left by a transient failure. |
| `template-selftest.yml` | none | push to `main`, any PR | Runs `tests/test_claude_pipeline_*.py` and shellcheck. No secrets. |

Supporting scripts live in `.github/claude/`: `report.sh` (the tail every implement and
fix run ends with), `resume.py` (the resume rules), `setup-project.sh` (the default
project setup).

## The labels

| Label | Meaning |
|---|---|
| `claude` | This issue is pipeline work. Without it nothing runs. **Add it last.** |
| `tier:fable-plan` | Plan first. Multi-module, architectural, ambiguous or large work. One extra pass, and it saves a wrong PR. |
| `tier:opus-direct` | Implement straight away. Single module, clear approach, small, unambiguous. |
| `plan-ready` | A Plan comment exists. Added by `claude-plan`; it is what starts the implementer. |
| `risk:review` | A human makes the merge decision. The default. |
| `risk:auto` | Eligible for auto-merge — only when `AUTO_MERGE` is `on`, and only alongside every other gate. |
| `do-not-merge` | Never merge automatically, whatever else is true. Overrides `risk:auto`. |

The tier names carry the model families they were tuned on; rename them if you like, but
rename them everywhere (both workflows' `if:` and the tests).

**`claude` is the release switch, so it goes on last.** Every `labeled` event
re-evaluates the trigger with the issue's full label set. Put `claude` on an issue that
has no tier and nothing happens; put a tier on an issue that has `claude` and it starts
at once. Release two or three issues at a time: each is up to 90 minutes of the model on
your subscription, and the subscription's usage wall is a *rate*.

Only people with triage access or above can add labels, which is what makes a label a
reasonable gate.

## Setup

1. **Install the Claude GitHub App and create the secret.** In Claude Code,
   `/install-github-app` installs the app on the repository and creates
   `CLAUDE_CODE_OAUTH_TOKEN`. It is the only secret this pipeline uses (§ Secrets).
2. **Create the labels:**

   ```bash
   gh label create claude            --color 7057ff --description "Pipeline work; add last"
   gh label create tier:fable-plan   --color 0e8a16 --description "Plan before implementing"
   gh label create tier:opus-direct  --color 1d76db --description "Implement directly"
   gh label create plan-ready        --color c2e0c6 --description "A Plan comment exists"
   gh label create risk:review       --color fbca04 --description "A human merges"
   gh label create risk:auto         --color d93f0b --description "Eligible for auto-merge"
   gh label create do-not-merge      --color b60205 --description "Never auto-merge"
   ```

3. **Set the repository variables** (Settings → Secrets and variables → Actions →
   Variables, or `gh variable set NAME --body VALUE`). All are optional.

   | Variable | Default | What it does |
   |---|---|---|
   | `SETUP_COMMAND` | auto-detect `package-lock.json` / `requirements.txt` | Shell run before the model, on the default branch's checkout. |
   | `TEST_COMMAND` | "the one CLAUDE.md names" | Named in the implementer's prompt as the command that must pass. |
   | `PYTHON_VERSION`, `NODE_VERSION` | unset (toolchain step skipped) | Version for `actions/setup-python` / `setup-node`. |
   | `CLAUDE_PLAN_MODEL` | `claude-fable-5-1` | Planner model id. |
   | `CLAUDE_IMPLEMENT_MODEL` | `claude-opus-5` | Implementer and fixer model id. |
   | `CLAUDE_REVIEW_MODEL` | `claude-fable-5-1` | Reviewer model id. Keep it different from the implementer's; the review job warns when they match. |
   | `CLAUDE_REVIEW_VERBOSE` | unset (off) | `on` prints every message of a review into the run log. A lever for chasing a failure, not a setting (§ When a review produces no verdict). |
   | `AUTO_MERGE` | unset (off) | `on` arms `claude-automerge.yml`. |
   | `AUTOMERGE_PROTECTED_PATHS` | empty | Extra path prefixes auto-merge must never touch (space-separated). |

   Then edit the three `# >>> CUSTOMISE` blocks in `claude-implement.yml`: a `services:`
   block if your tests need a database, toolchain steps beyond Python and Node, and the
   `--allowedTools` list, which must cover whatever `SETUP_COMMAND` and `TEST_COMMAND`
   run (the model can run only what is listed).
4. **Copy `CLAUDE.md.example` to `CLAUDE.md`** and fill it in. Keep the lines marked
   *(keep)*: the draft-first convention and the trust-boundary rule reach every run
   through this file, including the `fix` job, which has no prompt of its own.
5. **Actions permissions** (Settings → Actions → General):
   - The workflows declare their own `permissions:`, so the default token setting can
     stay at read-only.
   - **"Allow GitHub Actions to create and approve pull requests" can stay off**, and the
     pipeline is built to work with it off (§ The draft comes first). Turning it on
     grants *every* workflow the right to create **and approve** pull requests, which is
     a wide grant for a narrow need; an organisation may also refuse it above the
     repository. With it off, a run that ends before the model opened its draft will
     push its branch and say on the issue that no pull request could be opened.
6. **Open one small test issue**, label it `tier:opus-direct`, then `claude`. Watch the
   run. `claude-review` must run once before it can be selected as a required check.
7. **Branch protection** on the default branch (recommended): require a pull request,
   require the `claude-review` check and your CI check, and require one human approval.
   Restrict who can push to `.github/` with a CODEOWNERS entry. On GitHub Free, branch
   protection and rulesets are not available for *private* repositories; there, nothing
   can *require* a green check, and a red `claude-review` is advice to whoever merges.

## The draft comes first

The implementer is told to make a small first commit, push it, and immediately open a
draft pull request carrying `<!-- claude-implement-draft-first -->`, before doing any
real work. Not at the end.

The reason is a chain of facts about tokens. The App's token is revoked when the action's
own step ends, so anything that runs afterwards falls back to the workflow's
`GITHUB_TOKEN`, and that token may not be allowed to create pull requests (the setting
above). A run that finished its work and pushed five commits but did not open a pull
request is a branch nobody finds, and the tail cannot fix it. Opening the draft while the
model's token is alive closes that hole without any repository-wide grant; everything the
tail does afterwards is *promoting* a draft, which the workflow token can do.

It cannot be earlier than the first commit: GitHub refuses a pull request with nothing
between base and head.

The marker is a protocol read in three places, and each half fails silently alone:

| Reader | What it does with a draft-first draft |
|---|---|
| `.github/claude/report.sh` | On success **with every acceptance criterion ticked**, promotes it (`gh pr ready`, drops a `WIP:` title prefix, comments `<!-- claude-implement-finished -->`). On success with an unticked `- [ ]`, leaves it a draft and says how many remain: exit code 0 is not a claim the work is done. On failure, comments the `subtype=` record on it. |
| `claude-implement.yml` · `guard` | Resumes the branch behind it instead of reading it as somebody else's draft. |
| `.github/claude/resume.py` | Treats it as a candidate, and leaves it alone until a safety-net record exists, so the scheduler never dispatches a second run over one still going. |

**Drafts are not reviewed.** `claude-review` skips a pull request while it is a draft and
runs on `ready_for_review`. Reviewing a diff nobody claims is finished would request
changes on it and start the fix loop against it. This is also the cheapest way to iterate
on a pull request by hand: `gh pr ready --undo`, push freely, `gh pr ready` for one review
of the accumulated diff.

## When a run fails

Every implement and fix run ends with two steps under `if: always()`, so they run
*especially* when the model step failed:

1. **The transcript is kept** as an artifact for 14 days. The action hides the model's
   turns from the log (tool results can print secrets), so this is the only record of
   where a budget went. `gh run download <run-id>` fetches it; the result message at its
   end carries `subtype`, `num_turns` and the number of denied tool calls.
2. **The work is left where a person can find it** (`.github/claude/report.sh`):
   uncommitted edits become one `WIP:` commit marked as the workflow's; unpushed commits
   are pushed; commits without a pull request get a draft; a pull request gets a comment
   saying how the run ended; nothing committed gets a comment on the issue saying so.

   It authenticates twice, because the first token is already dead: it probes the App's
   token with `gh api rate_limit` and falls back to the workflow token when refused.
   `${{ a || b }}` cannot do this — a revoked token is a non-empty string.

   The tail runs the **default branch's** copy of the script, stashed into
   `$RUNNER_TEMP` before the model starts, so neither the model nor a pull request can
   rewrite the step that runs after it with a write token.

**Resuming by hand.** Comment on the draft:

```
@claude please continue implementing #<n>: read the issue, the Plan comment if there is
one, and the diff so far, then finish the remaining acceptance criteria and mark the
pull request ready for review.
```

**Restarting.** Close the draft, delete the branch, re-apply the tier label — or
`gh workflow run claude-implement.yml -f issue_number=<n>`. The issue must still carry
`claude`. Closed pull requests do not block a restart.

**Resuming on its own.** Every safety-net record carries
`<!-- claude-implement-safety-net subtype=<result subtype> -->`. Every six hours
`claude-resume` looks at open drafts with a pipeline marker and re-dispatches the issue
when the **latest** record names a retryable subtype, **no person has commented since the
pipeline last acted**, fewer than **three** attempts have been made, and the last one is
more than two hours old.

- Retryable is a positive list, `{error_during_execution, none}`, held in both scripts
  and bound together by a test. `error_max_turns` is a budget: another attempt on the
  same scope repeats it, so split the issue instead. `success` means the work is waiting
  on a person. `cancelled` means a person stopped it, and re-dispatching would undo that.
- "Since the pipeline last acted" is measured from the last resume dispatch, not the last
  failure record, so a comment posted while an attempt is in flight still stands it down.

## When a review produces no verdict

`claude-review` is red in two situations that mean opposite things. `request_changes` is
the check working. **No verdict at all** means nothing reviewed the pull request. Causes
that look identical from the outside: the turn-budget throw (below), the action refusing
a bot actor, the subscription's usage limit, and a spent Actions allowance. The transcript
artifact tells them apart; the step summary does not.

**One exemption, and only one.** The action refuses to run when its workflow file differs
from the default branch's, which is every pull request that edits `.github/workflows/`. In
that case the check passes and says on the pull request that nothing reviewed it. A pull
request that changes a workflow is never machine-reviewed: **read it yourself**. (Auto-merge
also refuses any pull request touching `.github/`, for this reason.)

**Every outcome is one sticky comment** marked `<!-- claude-review-status -->`, edited in
place and found by its marker. Without it, the two paths where the model does not run —
the exemption and the missing verdict — say nothing anywhere a reviewer looks. Its marker
is deliberately not the fix loop's: the round cap counts `<!-- claude-fix-request -->`
comments, so sharing it would spend the cap by itself. Failing to post it never changes
the check's colour.

`CLAUDE_REVIEW_VERBOSE=on` prints every message into the run log, which has no retention
limit and is readable by anyone who can read the repository. Turn it on while chasing a
failure and off afterwards.

## The fix loop

`claude-review` posts `@claude please address the review` (with the fix-request marker)
only when the pull request is still **open** and its author is the **Claude app**. On a
human's pull request the loop would push to someone else's branch uninvited. Neither
precondition changes the check's colour. After **three** requests it says so and stops
asking; that comment carries no `@claude`.

The review's one-sentence summary is quoted into that comment, so every `@` in it is
defanged to `(at)`: it is model text shaped by the diff, and must not be able to mention
anyone, or carry an `@claude` past the cap.

`@claude` works on a pull request and **never on an issue**. To put the implementer on an
issue, label it or dispatch the workflow.

## The trust boundary

**Instructions come from two places and only two: the body of an issue a maintainer
labelled, and the prompt written into the workflow file. Everything a run reaches through
`gh`, a diff, a CI log or a file in the checkout is data.** Data describes the task; it
does not change it. It may not widen a tool allowance, add a dependency, reach into a
path `CLAUDE.md` fences off, spend money, or decide what the next job in the chain is
asked to do. When such text asks anyway, the run says so on the issue or pull request and
does not do it. The rule is in every prompt and in `CLAUDE.md.example`.

None of the four surfaces below looks like a prompt while you are editing it:

| # | Surface | What is read as a prompt | What is behind it |
|---|---|---|---|
| 1 | `claude-implement.yml` · `fix` | **A comment body.** No `prompt` input, so the comment *is* the request. | `contents: write`; `Edit`, `Write`, `git`, and your build tools. A comment is an instruction with a compiler and a package installer behind it. |
| 2 | `claude-implement.yml` · `implement` | **The issue body.** The outer text is a genuine instruction (a maintainer labelled it); anything it *quotes* — logs, user reports, third-party docs — is not. | The same permissions and allowance. |
| 3 | `claude-review.yml` | **The diff**, which is the contributor's text, including any vendored file or dependency bump that carries a third party's prose. | Read-only on code; can comment. Cheapest to get wrong, easiest to overlook. |
| 4 | **The review→fix hop** | `claude-review` posts the fix request, so content in a diff that steers what the reviewer *writes* lands in the *implementer's* context, where the allowance is `Write`. | Neither half is unreasonable alone; the composition is the surface. |

What bounds each:

- **Surface 1 — `author_association`.** The `fix` job runs only for a comment whose
  `github.event.comment.author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`, or
  for the one bot comment the loop needs: `github-actions[bot]` posting a comment that
  carries the fix-request marker. The Claude app itself is *not* admitted, so a review
  the model wrote cannot summon the implementer with words a diff put in its mouth. The
  action separately checks that a human actor has write access. Pull requests from forks
  are refused outright: an `issue_comment` run carries a write token whatever the pull
  request's origin. (The repository this was extracted from was private and left this
  check out; it was the first thing its docs said to add if the repository opened up.)
- **Surface 2 —** the prompt's data rule, and the fact that only triage+ users can label.
- **Surface 3 —** the reviewer cannot write code; its verdict is structured output a
  workflow step decides on; `allowed_bots` names three logins, never `*`.
- **Surface 4 —** the fix request's own text is fixed by the workflow, the summary is
  defanged, and the round cap lives in the workflow, not in the model. Use a different
  model for the reviewer: it is also the cheapest defence against a shared blind spot.

**A pull request cannot be allowed to widen its own tool allowance.** The action loads
`.claude/settings.json` **and `.claude/settings.local.json` from the checkout it runs
on**, and the local file carries `permissions.allow` and `enabledMcpjsonServers`. A
committed one would widen the allowance of the job reviewing it — the one place where
data becomes configuration without passing a person. `.gitignore` names it, and
`tests/test_claude_pipeline_trust_boundary.py` fails if it is ever tracked. The stronger
lever, if you need it, is `--setting-sources user` in `claude_args`, which also turns
vendored skills off.

**A pipeline run cannot change this pipeline.** GitHub refuses any push from an App or
`GITHUB_TOKEN` that creates or updates a file under `.github/workflows/`. A run asked to
edit a workflow can write the patch into the PR body; a person with `workflow` scope
applies it.

## Skills are vendored, not installed

The `implement`, `fix` and `review` jobs can invoke skills in `.claude/skills/` of the
checkout. Copy a skill into the repository; do **not** use the action's `plugins` /
`plugin_marketplaces` inputs:

- `plugin_marketplaces` accepts a `https://….git` URL with no ref, tag or sha, and
  `claude plugin install name@x` reads `@x` as a marketplace, not a version. An installed
  plugin therefore resolves to someone else's default-branch HEAD on every run: a third
  party deciding what reviews your code, with no diff anywhere.
- A copy in the tree is pinned by your commit, reviewable, needs no network at run time,
  and costs no install time.

To vendor one:

1. Copy the skill directory from the upstream repository **at a pinned commit or tag**
   into `.claude/skills/<name>/`, and record the source URL and ref (in a
   `THIRD-PARTY-NOTICES.md` or the skill's own README).
2. Copy the upstream **`LICENSE`** into the same directory. Vendoring is redistribution.
3. Rewrite plugin-namespaced cross-references (`some-plugin:other-skill` does not
   resolve for a skill loaded from a repository) and disclose that you modified it.
4. Make sure `Skill` is on `--allowedTools` for each job that should use it. Discovery is
   automatic, but the allowlist is exhaustive: without `Skill` the model sees every
   skill's description and can invoke none of them, and nothing in the log says so.
5. Keep the list short. Every skill's name and description is injected into every run
   whether it fires or not, and a skill written for another stack can instruct the agent
   to do things your `CLAUDE.md` forbids. Where they disagree, `CLAUDE.md` should say it
   wins.

## Secrets

**`CLAUDE_CODE_OAUTH_TOKEN` is the one auth secret.** No workflow names the API-key
credential, and none should. The action maps its API-key input straight into the model's
environment, and an unset secret renders as the empty string, so naming a secret that
does not exist sets an *empty* credential rather than no credential. If you move to an
API key — a billing decision: the OAuth token draws on a Claude subscription, an API key
bills per token — switch deliberately, one credential at a time, and update the test
that forbids the name.

No secret value belongs anywhere but GitHub's secrets UI.

## Pinned versions

`anthropics/claude-code-action` is pinned to an exact release (`v1.0.216`), not `@v1`.
A moving tag on the one third-party action that holds a write token means the model's
tool surface and auth handling can change under a workflow nobody edited. Bumping it is
a one-line diff, which is the point; the tests require every step to use the same pin.
The first-party `actions/*` steps stay on their major tags.

## The auto-merge gate

`claude-automerge.yml` merges only when **all** of these hold:

1. repository variable `AUTO_MERGE` is `on` (unset is off);
2. the pull request carries `risk:auto` and not `do-not-merge`;
3. it is open, not a draft, not from a fork, and mergeable;
4. every check on the head commit is green (`SKIPPED`/`NEUTRAL` count; still-running does not);
5. the `claude-review` check specifically concluded `SUCCESS` — no entry means never reviewed;
6. the author is the Claude app;
7. no changed file is under a protected path: `.github/`, `.claude/`, `CLAUDE.md`, plus
   `AUTOMERGE_PROTECTED_PATHS`;
8. the head has not moved since the gates were read (`--match-head-commit`).

Every trigger runs the workflow from the default branch, so a pull request cannot loosen
its own gate. It contains no model call: "may this merge?" has a definite answer.

## How to stop it

Smallest lever first.

- **One pull request:** add `do-not-merge`.
- **A pipeline draft:** close it. To keep it but stop automatic attempts, comment on it:
  any human comment stands `claude-resume` down.
- **One issue:** remove `claude`, or `plan-ready` / `tier:*`.
- **A run going wrong right now:** cancel it in the Actions tab. The tail still pushes
  what it had, but records `subtype=cancelled`, which is never resumed.
- **All automatic resumes:** `gh workflow disable claude-resume.yml`.
- **All auto-merging:** `gh variable set AUTO_MERGE --body off`.
- **Everything:** `gh workflow disable` each of the five `claude-*.yml`.

## Failure modes we hit

Each of these happened on the repository this was extracted from, mostly silently. Most
are now a guard in a workflow and a test in `tests/`.

- **`--max-turns` is not a cost limit.** It is checked *after* the model finishes: the
  action compares the result's `num_turns` to the cap and throws, before it writes
  `structured_output`. A review that finished and approved at 32 turns against a cap of
  20 went red for a missing verdict; a planner that posted its Plan at 78 turns against
  60 failed and then told the issue no plan existed. The SDK's own turn counter and the
  result's `num_turns` are not the same counter. Set the cap with slack and use
  `timeout-minutes` as the bound — sized to pay for the cap, because a timeout kills the
  run before the transcript and the report are kept.
- **A red job is not a missing result.** Ask the issue or pull request what is actually
  on it (`claude-plan`'s "Did the plan land?" step) before telling anyone it failed.
- **The App's token is revoked before the `if: always()` tail runs.** Every `gh` call
  answered 401, the branch was pushed anyway (git used checkout's credential), no draft
  was opened, the issue was not told, and the step went green. Probe the token and fall
  back.
- **The workflow token may not open pull requests.** A run that finished, pushed five
  commits and opened no pull request reported success and orphaned its work. Hence the
  draft comes first.
- **Three spellings of one bot.** `gh` reports the Claude app as `app/claude` (a pull
  request's author, GraphQL), `claude` (comments and reviews) and `claude[bot]` (REST).
  A gate that listed two of them declined a fix request on the app's own pull request
  with "the author is app/claude, not the Claude app". Every comparison now strips
  `app/` and `[bot]` (neither is legal in a human login) and the lists carry all three.
- **Bot actors are refused before the model runs.** The action rejects a non-human actor
  not in `allowed_bots` in about two seconds, which looks exactly like a review failure.
  The planner's `plan-ready` is added by `claude[bot]`; a fix push is attributed to
  `github-actions[bot]`. Both must be named; `*` must never be.
- **Reviews with no verdict reached nobody.** Two pull requests were merged with a red
  "no verdict" check and nothing on either page saying so; one cost a few dollars and 29
  turns, the other never reached the model. Hence the transcript artifact and the sticky
  status comment on every outcome.
- **A merged pull request was asked to fix itself.** The review lands minutes after
  `opened`; a human merged in that window and the fix job spent a full setup on a branch
  already in `main`. Both the review and the `fix` job now check the pull request is open.
- **"Commit suggestion" applies only the fenced lines.** A reviewer put the new
  comparison in a ```` ```suggestion ```` block and the bookkeeping it needed in the prose
  beside it; clicking the button committed half a change and a `NameError`. The review
  prompt now says to put the whole change in the block. After applying suggestions, run
  the tests before pushing — and batch them, because each click is a push and each push
  is a review.
- **Same-model review is weak.** The implementing model reviewing its own branch missed
  two real credential leaks that a different model found at once, and the branch's own
  new tests missed them too. Keep `CLAUDE_REVIEW_MODEL` different from the implementer's.
- **Actions minutes running out looks like a failed check.** When the plan's included
  minutes are spent, a run is a few-second job with zero steps and no log, concluding
  `failure`. A red `claude-review` in that state means *never ran*, not *rejected*.
  Check the billing page before believing a check. The resume cron is the biggest quiet
  consumer: every tick bills at least a minute.
- **The concurrency queue keeps one pending run.** A workflow-level concurrency group on
  a workflow that also receives `issue_comment` lets an unrelated comment replace a
  queued `labeled` run. Concurrency is therefore set per job, where only runs that will
  actually execute take a slot.
- **The action ignores `branch_name_template` in automation mode**, so the workflow
  creates `claude/issue-<n>` itself, and resumes it if it already exists.
- **A pull request's state can lag its branch.** GitHub once showed a stale head and
  "merging is blocked" for half an hour after a bot push with no `synchronize` event.
  `gh pr close <n> && gh pr reopen <n>` refreshed it at once.
- **A pull request was merged before its last commit was pushed.** The fix it carried
  never reached `main`. Diff the branch against `main` before assuming a merge carried
  everything.
- **A large contract file is paid for on every turn.** `CLAUDE.md` is loaded into every
  turn; a 160 KB one was carried two hundred times per run. Keep it a contract plus an
  index, and move the narrative to docs read on demand.
