---
description: Browse the Open Education Club catalog and start a course from it with the student.
agent: botai
---

Start a course from the SourceCraft Open Education Club catalog together with
the student. Follow the `starting-course-from-education-club` skill and AGENTS.md
exactly:

1. If the `education-club` MCP is not available, tell the student to enable it
   (`make education-club`, see docs/education-club.md) and stop.
2. Call `education-club_list_courses()` and show the student the catalog.
3. Shortlist courses with the student; call `education-club_get_course(<slug>)`
   for each candidate and read the README (audience, prerequisites, format).
4. Run the student consent gate (level, feedback delivery, graded vs practice)
   and record it via `maintaining-course-progress`.
5. Call `education-club_fetch_course(<slug>, courses)` to pull the course into
   `courses/<slug>/`, then map the syllabus and start co-learning.

Session input: $ARGUMENTS (course slug or topic, if the student named one). Do
not improvise the catalog; the MCP is the source of truth.
