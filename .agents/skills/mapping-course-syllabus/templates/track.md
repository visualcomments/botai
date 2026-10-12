# Course Track Template

One file per course at `courses/<slug>/track.md`. Every row must trace to a
course document; mark anything not stated in the course as `unstated`.

```markdown
# Track: <course-slug>

Source documents: <list files this track was built from>

## Modules

| # | Module | Learning objectives | Prerequisites (stated) | Status |
| --- | --- | --- | --- | --- |
| 1 | <module> | <objective> | <prereq or "none"> | new / learning / practising / mastered / blocked |

## Graded vs practice

- Graded: <from assignment sheets>
- Practice: <from assignment sheets>

## Gaps and flags

- <module> assumes <topic> but the course does not teach it — flag for supplement mode
- <module> prerequisites: unstated — confirm with the student before teaching
```
