---
description: Review a student's submission against the course rubric.
agent: botai
---

Review the student's submission: $ARGUMENTS.

1. Locate the student's attempt under `courses/<course>/assignments/` (or where
   the student says it is).
2. Run the `reviewer` agent using `giving-feedback`: what is right, what needs
   work, and how to fix it themselves.
3. Respect the recorded delivery preference. For graded tasks, never reveal the
   answer - end at the fix strategy.
4. Record the review and the demonstrated level in the progress record.

Route the review through the reviewer subagent so its edit-deny guard applies.
