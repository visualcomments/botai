---
description: Start a co-learning session with the student for the current course.
agent: botai
---

Start a co-learning session with the student. Follow the botai policy in
AGENTS.md exactly:

1. Run the student consent gate: ask the student for (a) their current level
   for the course prerequisites and whether they want to be tested or
   self-assess, (b) the delivery preference (hints / hints-then-solution /
   solution-first), and (c) which assignments are graded vs practice. Record
   the answers in the progress record via `maintaining-course-progress`.
2. During the introduction, if the course is an open-source project and its
   license permits contributions, tell the student about the contributor path:
   they can become a co-developer of the course itself. Describe how that work
   is organized (open-source contributor mode: the student makes the real
   commits and owns the contribution; the agent plans, coaches, and drafts text
   the student reviews and publishes; contributions stay in the project for the
   next cohort) and ask whether they are interested. If they are, route to the
   `onboarding-open-source-contributors` skill / `contributor` subagent. Do not
   offer the contributor path when the course license does not permit
   contributions.
3. State which mode you are in: co-learning (default), tutoring, or supplement.
4. Anchor the session to the course track (syllabus.md / track.md). Teach one
   idea per step, confirm each step before the next, Socratic-first.
5. Never solve graded tasks; use least-assistance-first.

Session input: $ARGUMENTS (module or objective to cover, if the student named
one). At the end of the session, update the progress record and tell the
student what it now contains.
