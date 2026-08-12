---
description: Work on the gated local practice track (course-lab). Run make lab-* targets as instructed by the student.
agent: botai
---

The student wants to work on the local practice track: $ARGUMENTS.

This is gated material. Per AGENTS.md:

1. Confirm the student has, in THIS session, asked to work on the practice
   track. If they have not, ask before doing anything.
2. Do NOT read `docs/course-lab.md`, the solution files, or the answer keys
   under `course-lab/solutions/` before the student has made their own attempt.
   Treat solution keys as graded material - for checking after an attempt, not
   reading first.
3. Help the student break the lab task down and coach each step without
   reaching for the answer key.
4. After the student has attempted the task, you may check against the solution
   key to give feedback.

Use `make lab-*` targets only as the student directs (lab-lab, lab-check,
lab-clean). Never run lab-answers on your own.
