---
description: Scaffold a new course and map its syllabus into a track.
agent: botai
---

Scaffold a new course. The course name/slug from the user is: $ARGUMENTS.

1. Run `make new-course NAME=<slug>` to scaffold the course layout under
   `courses/<slug>/`.
2. Ask the student to point at the course documents (syllabus, module files,
   assignment sheets) if they are available, or note that the scaffold is empty
   and the syllabus.md needs filling in.
3. If course documents exist, run the `mapper` agent to turn them into a track
   of modules, objectives, and prerequisites (`mapping-course-syllabus`), and
   record the graded vs practice split.
4. Report the scaffolded structure and the next steps.

Do not invent course content or objectives that are not in the documents.
