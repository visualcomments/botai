---
name: mapping-course-syllabus
description: Turn the course's own documents (syllabus, module handouts, lesson slides, assignment sheets) into a working track of modules, learning objectives, and prerequisites that the co-learner agent can teach against. Use when starting a new course, when the student hands over course materials, when a module's prerequisites are unclear, or when the agent needs a scope map for a session.
verified: 2026-08-12
---

# Mapping Course Syllabus

The course documents are the source of truth. This skill turns them into a
working track — modules, objectives, prerequisites, and the order in which they
must be taught — without inventing anything the course does not contain.

## When to Use

- Starting a new course: the student hands over a syllabus, module PDFs, slides,
  or an assignment sheet
- A module's prerequisites are unclear and teaching keeps hitting gaps
- The agent needs a scope map to stay on-track during a session
- Before `planning-study-sessions`: the track is the input it plans from

## When NOT to Use

- **Writing or rewriting the course itself** — the course belongs to its
  authors; this skill only reads it and structures it
- **Deciding what a student knows** — that is `assessing-understanding`
- **Deciding what to teach when the course omits it** — that is
  `providing-supplementary-material` (and must be labeled as a supplement)

## Rules of the map

1. **Extract, do not invent.** Every objective, module, and prerequisite in the
   track must trace to a line in the course documents. If the course does not
   state a prerequisite, mark it `unstated` and flag it — do not guess it into
   the track.
2. **Order is constraints.** A module whose prerequisites are unmet cannot be
   taught cleanly. The track's ordering comes from stated prerequisites, not
   from document order alone.
3. **Gaps are findings.** When the course jumps (module 3 assumes module 2's
   topic but never teaches it), record the gap in the track and surface it to
   the student. The gap is exactly what the supplement mode exists to fill.
4. **Never fabricate objectives.** If the course never says "student can
   reverse a linked list", the track must not say it. Use the course's own
   wording where possible.
5. **Graded vs practice stays explicit.** Record from the assignment sheets
   which tasks are graded; this feeds the `maintaining-course-progress` split.

## Workflow

1. Read the syllabus and module documents. Note the source file for each claim.
2. Build the module list with objectives and prerequisites (see
   `templates/track.md`).
3. Validate order against stated prerequisites; mark `unstated` where missing.
4. Record gaps and flags in a separate "gaps" section.
5. Write the track to `courses/<slug>/track.md` and record it in the progress
   file via `maintaining-course-progress`.

## References

- `maintaining-course-progress` — the track feeds the durable record
- `planning-study-sessions` — consumes the track to schedule work
- `providing-supplementary-material` — fills the gaps this skill flags
