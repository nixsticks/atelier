---
description: Log a GitHub issue against nixsticks/atelier with appropriate labels
argument-hint: [short description of the issue, or omit to use recent context]
---

You are logging a GitHub issue against `nixsticks/atelier`.

User intent: $ARGUMENTS

## What to do

1. **Figure out what to log.** If `$ARGUMENTS` is empty or vague (e.g. "log this"), look at the most recent turns of the current conversation to identify what the user is referring to. If still ambiguous, ask one clarifying question with AskUserQuestion before proceeding.

2. **Draft the issue.** Write:
   - **Title**: short, imperative, under ~70 chars (e.g. "Hide 'no prompt selected' empty state when a prompt is loaded")
   - **Body**: structured as:
     ```
     ## Context
     <1-3 sentences on what the user observed or wants>

     ## Proposed change
     <1-3 sentences on what should happen>

     ## Notes
     <optional: file paths, line numbers, related code — use file_path:line_number format>
     ```
   - Reference specific files/lines you already know about. Don't go searching the codebase just to fill this out — keep it lightweight.

3. **Pick labels.** Available labels:
   - `nice-to-have` — non-critical, can be deferred (use this for most UI polish)
   - `v1.5` — targeted for next minor release
   - `v2` — targeted for the MJ-API integration milestone
   - `ui` — frontend / visual / UX
   - `bug` — something is broken
   - `enhancement` — new capability or improvement
   - `documentation` — docs/README/CLAUDE.md changes

   Apply 1-3 labels. Default combo for UI polish: `ui` + `nice-to-have`. If the user mentioned a version target ("v1.5", "v2"), include that label.

4. **Create the issue:**
   ```bash
   gh issue create \
     --repo nixsticks/atelier \
     --title "<title>" \
     --label "<label1>,<label2>" \
     --body "$(cat <<'EOF'
   <body>
   EOF
   )"
   ```

5. **Confirm.** Output one line: the issue number, title, and URL. Nothing else. Example:
   ```
   #3 "Hide empty state when prompt loaded" → https://github.com/nixsticks/atelier/issues/3
   ```

## Rules

- Don't overthink it. The point of this command is to capture ideas fast so they're not lost.
- Don't start fixing the issue. Just log it.
- Don't use TaskCreate for this — it's a single action.
- If the user says "log these" (plural) and there are multiple distinct things, create one issue per thing.
