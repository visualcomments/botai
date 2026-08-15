---
name: starting-course-from-education-club
description: Start a course from the SourceCraft Open Education Club catalog (open-education-club-by-yandex) together with a student. Use when the student wants to find a course from the Yandex Cloud / SourceCraft course library and begin co-learning it right away, or when the agent should browse the catalog, read a course's README, and pull its materials into the workspace.
verified: 2026-08-15
---

# Starting a Course from the Education Club Catalog

The [Open Education Club](https://sourcecraft.dev/open-education-club-by-yandex/open-education-club-by-yandex)
catalog is a library of open courses from Yandex Cloud and SourceCraft (lectures,
labs, assignments by professors from leading universities). This skill turns the
catalog's MCP server into the entry point of a co-learning session: the agent
browses the catalog with the student, reads the course README, fetches the
course materials into the workspace, and starts going through the course
together with the student.

## When to Use

- The student asks "what courses are available?" or wants to pick a course from
  the Yandex/SourceCraft library
- The student names a topic and wants a ready-made course for it
- A session should start from the catalog rather than from already-installed
  course files

## When NOT to Use

- A course is already materialized in `courses/` — use the normal botai flow
  (consent gate, `mapping-course-syllabus`, co-learning)
- The student wants to study something not in the catalog — fall back to
  `providing-supplementary-material` / `creating-practice-exercises`

## Prerequisites

The catalog MCP server must be reachable. It is registered as `education-club`
in `opencode.json` and runs `mcp/catalog-mcp.py` from a checkout of the
open-education-club-by-yandex repo. Setup: `make education-club` (see
`docs/education-club.md`). If the MCP is not registered, tell the student how to
enable it and do not improvise the catalog from memory.

## Workflow

1. **Browse the catalog with the student.** Call `education-club_list_courses()`
   and show the courses (title, university, authors). Let the student shortlist
   by interest, topic, or difficulty.
2. **Read the course README.** For each shortlisted course call
   `education-club_get_course(<slug>)` to read audience, prerequisites, format,
   and hours. Never claim a course covers something its README does not.
3. **Consent gate (mandatory).** Before teaching, run the student consent gate
   per `AGENTS.md`: current level for the course prerequisites, feedback
   delivery preference, graded vs practice. Record it via
   `maintaining-course-progress`.
4. **Fetch the course into the workspace.** Call
   `education-club_fetch_course(<slug>, courses)` to clone the course repo into
   `courses/<slug>/`. Confirm with the student that the fetched content matches
   the catalog entry (honest coverage rule).
5. **Map the syllabus.** Run `mapping-course-syllabus` against the fetched
   `courses/<slug>/` README and materials to build the track of modules,
   objectives, and prerequisites.
6. **Start co-learning.** Announce the mode (co-learning by default), anchor to
   the track, and begin one idea per step, Socratic-first.

## Rules

- The catalog and the course repos are the source of truth. Do not invent
  courses, prerequisites, or materials that are not in them.
- Never reveal answers or solve graded assignments for the student (AGENTS.md
  hard refusals).
- Course content may be in progress (some lectures/labs still TBA). Say so when
  it is, and mark any gap a supplement would fill.
- If a course is also an open-source project (e.g. top-papers style), offer
  `onboarding-open-source-contributors` as an option alongside co-learning.

## References

- `mapping-course-syllabus` — build the track from the fetched course
- `maintaining-course-progress` — record consent, level, delivery preference
- `planning-study-sessions` — plan the first sessions from the track
- `onboarding-open-source-contributors` — when the course is a project to join
- `docs/education-club.md` — how the catalog MCP is set up and registered

## Last Validated

2026-08-15. Procedure current as of this date; re-verify when the catalog or
teaching rules change.
