# Skill: lost-work audit

Answers "did we lose anything?" after a merge, a gap, or a branch switch. A hunch is not an
answer; this is a sweep plus a comparison.

## Why it is needed

Work goes missing here in three ways that all look clean from a `git status`:

1. **An untracked doc.** `git diff` reports no change, `git checkout <branch>` leaves it
   untouched (a branch switch does not move untracked files), and a `git add -A` from a
   sibling branch can sweep it into the wrong commit. Every git command reads the working
   tree as if the file were real.
2. **A branch that was never merged.** It sits there holding the only copy.
3. **A stash.** Dropped or forgotten.

## The sweep

```bash
git branch -a --no-merged dev            # branches not merged into integration
git stash list                           # anything parked
git status --short | grep '^??'          # untracked, visible files
git log --oneline dev..HEAD              # what this branch has that dev does not
```

For each unmerged branch, compare by patch-id rather than by diff, so you can tell "merged
by another route" (same change, different commit) from "genuinely missing":

```bash
git log dev..<branch> --format=%H | while read c; do
  git show "$c" | git patch-id --stable
done
```

Run the same over `dev` and compare the patch-ids. A matching patch-id means the change
landed under another commit; an unmatched one means it is missing.

## The trackedness trap

To test whether a doc is committed, use `git ls-files <path>` (empty result = not tracked)
and `git show HEAD:<path>` (errors on a path absent from the commit, which is the answer).
**Never** test trackedness by diffing the working file against itself: that always returns
identical and reads as "already committed". After a `git checkout` the illusion survives,
because an untracked file is not touched by a branch switch.

When a whole spec set is added or merged, assert trackedness for every file in it, not just
the one you were editing. The missed file is by definition the one you were not looking at.

## Reading a parked branch

A non-merged branch with a handoff doc is **parked**, not dropped. Report it as parked with
the handoff path, not as lost.
