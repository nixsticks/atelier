---
description: List open atelier issues and start work on one
argument-hint: [optional issue number, or filter like "ui" or "v1.5"]
---

You are picking up a GitHub issue from `nixsticks/atelier` to work on in this session.

User input: $ARGUMENTS

## What to do

1. **Determine what to fetch.**
   - If `$ARGUMENTS` is a number (e.g. `7`), go straight to step 3 with that issue number.
   - If `$ARGUMENTS` is a label name (e.g. `ui`, `v1.5`, `nice-to-have`), filter the list by that label.
   - If `$ARGUMENTS` is empty, list all open issues.

2. **List open issues:**
   ```bash
   gh issue list \
     --repo nixsticks/atelier \
     --state open \
     --limit 30 \
     --json number,title,labels,createdAt \
     [--label "<label>" if filtering]
   ```

   Display them as a compact numbered list:
   ```
   #3  [ui, nice-to-have]  Hide 'no prompt selected' when prompt is loaded   (2d ago)
   #5  [v2]                Wire MJ /imagine button into node detail panel    (1d ago)
   ```

   Then ask the user — using AskUserQuestion — which issue to pick up. Offer up to 4 options, with the most-recently-created or smallest-scope ones first. If there are more than 4, mention the rest in the question text.

3. **Read the chosen issue in full:**
   ```bash
   gh issue view <num> --repo nixsticks/atelier --comments
   ```

4. **Plan the work.** Read the relevant files referenced in the issue body (or grep for them if not specified). Then enter plan mode (ExitPlanMode after planning) to confirm the approach with the user before writing code.

5. **Once approved, do the work** — code changes, tests if applicable. When done, ask the user whether to:
   - Open a PR that closes the issue (`Closes #<num>` in the PR body)
   - Commit straight to main (small fixes only)
   - Just leave the changes uncommitted for review

## Rules

- Always read the issue body before planning. The issue may have context that changes the approach.
- Don't pick up multiple issues in one session unless the user explicitly asks. Focus is more valuable than throughput here.
- If the issue is stale or unclear, say so and ask the user before spending tokens on it.
