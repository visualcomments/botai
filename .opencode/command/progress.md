---
description: Summarize the progress record for a course.
agent: botai
---

Summarize the learning progress for the course: $ARGUMENTS.

1. Read the progress record at `progress/<course>.md` (the course slug comes
   from $ARGUMENTS).
2. Produce a progress report following `reporting-learning-progress`: overview,
   what is complete, what is in progress, what is blocked, mastery level per
   objective, and recommended next steps.
3. If the record is missing or thin, run `make progress COURSE=<slug>` and say
   plainly that the record has no evidence yet - do not fabricate mastery.
4. Offer the summary to the student and ask whether to share it with a teacher.

Report mastery, not activity. Never include student work or graded answers.
