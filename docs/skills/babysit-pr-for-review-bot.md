# Skill: babysit a PR for the review bot

Keep watching a pull request, fix what the review bot raises, and stop only when the bot has
no unresolved comments and its score is at target (5 out of 5). This is a loop, not a check.

The bot on these repos is **greptile-apps**. It reports each finding as an inline review
comment with a severity badge (P1, P2) and, on the PR, a confidence score visible in its
GitHub UI. The score is **not** in the REST payload (its review body is empty, and issue
comments are zero), so the score is read from the GitHub UI or the bot's check run, not from
`gh api`.

## Hard rule: never comment on the repo

An agent babysitting here **must not** post any comment, reply, or review on GitHub. This is
not a preference. Posting a comment can dismiss or disturb the review bot's review state, and
a dismissed review reads as resolved when nothing was. The agent's only GitHub write is a
push of a fix commit to the PR head branch, plus (only when it is the bot's own thread) the
thread resolution the bot's workflow allows.

Concretely, the agent may:

- push fix commits to the PR head branch
- read anything

The agent may **not**:

- post a PR comment, a review comment, a review, or a reply on any thread
- resolve a thread that the human owns
- use any `gh` write command other than `git push` to the head branch

If a finding needs a written answer, report it to the human in chat and let them write it.
The human owns the conversation with the bot; the agent owns the code.

## The loop

1. **Snapshot** the PR: unresolved bot comments, CI status, mergeability. Run
   `python3 scripts/pr-babysit.py --pr auto` (in this directory) or use plain `gh`.
2. **If the bot has no unresolved comments and the score is at target**, report ready and
   keep watching while the PR is open. A clean snapshot is a milestone, not a stop.
3. **For each unresolved bot finding**, decide: is it a code fix, or does it conflict with an
   accepted decision?
   - **Code fix**: patch locally, commit, push, then re-snapshot. The push retriggers the bot.
   - **Conflicts with a spec/ADR**: do NOT silently change the spec to satisfy the bot. Stop
     and take it to the human. See "When the bot disagrees with a decision" below.
4. **CI failures**: classify branch-related vs flaky. Fix only branch-related. A review fix
   commit retriggers CI, so do not rerun checks on a SHA you are about to replace.
5. **After every push**, immediately re-snapshot and continue the loop in the same turn. A
   push is not a stop condition.
6. **Stop** only when the PR is merged or closed, or when a blocker needs the human (below).

## When the bot disagrees with a decision

The bot does not know which decisions were deliberately made. When it flags something that an
ADR or a spec section records as settled, that is a conflict, and the worst-case path:

1. Do not "fix" it by editing the code to match the bot. That reverts a human decision.
2. Do not edit the spec to match the bot. That lets a bot author design.
3. Take it to the human in chat: name the finding, name the ADR/spec section it conflicts
   with, and ask whether the decision stands or should be revised.
4. If the human revises the decision, update spec + ADR + code together (the repo's standing
   rule), then push. If the decision stands, the finding is a false positive: the human may
   want to note it to the bot, in their own words — the agent does not.

This is deliberately the worst-case path and should be rare. Most bot findings are ordinary
code defects and are fixed directly.

## Prove the fix before pushing it

A fix is not done when the test passes. A test that passes because it never reached the code
under test is worse than no test: it reports green while the bot keeps finding the same bug
one layer down, one round at a time. The review bot reads the code fresh each push and will
keep surfacing what a vacuous test hid.

For every fix, and especially for a **race or interleaving** finding:

1. **Reproduce the exact interleaving deterministically.** Do not rely on `sleep()` timing to
   hit a race; it usually runs the happy path and passes for the wrong reason. Drive the
   interleaving directly — replace the blocking call with a future you control, then complete
   it and cancel the awaiting task in the same loop turn. The dangerous window is when the
   inner task has ALREADY finished at the moment the cancel is delivered: `asyncio.shield`
   still raises `CancelledError` to the awaiter whenever a cancel is pending, but the inner
   task's result is now available, and code that breaks out of the wait without reading it
   records the completed work as failed. So the test must complete the inner task and cancel
   in one tick, and the assertion must check that the completed work was recorded, not lost.
2. **Prove the test fails before the fix.** Revert the fix in a scratch copy of the file (or
   `git stash` the hunk), run only the new test, and confirm it fails with the symptom the bot
   named. Restore the fix and confirm it passes. A test that is green on both is not a test.
3. **State the mechanism in the commit message**, naming the interleaving, so the next reader
   can re-derive the test rather than trust it.

## When the same area keeps yielding findings

If the bot returns a fresh genuine defect in the *same function* across several consecutive
pushes, stop patching and say so to the human. Each patch that fixes one interleaving can open
the next, and the loop does not converge. Name the pattern: "this is the Nth round on
`<function>`; the fixes are chasing symptoms, the design needs a rewrite." Then propose the
clean design and get a ruling before writing more code. Repeated findings in one spot are a
design signal, not a dozen separate bugs.

The concrete tell from this repo: `send()`'s cancellation handling took four rounds because
each fix guessed the pending entry's fate at cancel time, when the answer does not exist yet
(`asyncio.to_thread` cannot be interrupted, so the worker's result is unknown until it
returns). One accepted design (let the send finish; the real result decides the entry) closed
the whole class at once. Reach for the structural reading before the fifth patch.

## Target

Aim for no unresolved bot comments and the bot's score at 5 out of 5. Do not declare success
from "no new comments since I looked" — check the bot's actual state after each push, because
a fix push retriggers a fresh review.

## Files

- `scripts/pr-babysit.py` — read-only snapshot of PR state: unresolved bot comments, CI,
  mergeability. Never writes to GitHub.
