---
name: providing-supplementary-material
description: Detect gaps and weaknesses in the course material and prepare additional material — explanations, examples, practice, external references — to fill them, always labeled as supplements with a source. Use when the course is unclear, incomplete, or wrong; when the student asks "the course does not explain this"; or when a gap found by mapping-course-syllabus needs filling.
verified: 2026-08-12
---

# Providing Supplementary Material

The course is the track. This skill exists for the gaps the track has: where
the course is unclear, incomplete, or wrong. It prepares material that fills
the gap — and it labels that material as a supplement with a source, because a
supplement pretending to be the course is how students get taught things the
course never intended.

## When to Use

- The course text is unclear and re-explanation is not enough
- The course jumps over a prerequisite it assumes (flagged by
  `mapping-course-syllabus`)
- The course states something that is factually wrong
- The student asks "the course does not cover this, but the assignment needs
  it"

## When NOT to Use

- **Re-explaining course material** — that is `explaining-concepts`; supplement
  is for what the course lacks, not what it covers badly-worded
- **Replacing the course** — never. The course stays the syllabus; the
  supplement is labeled additional material
- **Anything the course covers later** — say where it is covered instead,
  unless the student asks for a preview

## Method

1. **Confirm the gap honestly.** State what the course does not cover, with the
   course text as evidence. Do not silently substitute your own version.
2. **Choose the fill.** Matches the gap: a plain-language explanation, a worked
   example, an exercise set, or an external reference. Prefer the course's own
   notation where it exists.
3. **Label it.** Every supplement states, in the material itself: this extends
   beyond the course, and it comes from <source>.
4. **Source everything.** No invented citations. If no real source exists, say
   so — the material is then flagged as the agent's own explanation, not
   attributed.
5. **Record it.** Note the supplement in the progress file so a teacher can
   review what the course was missing.

## Output shape

```
Supplement for: <module/topic>   (why: <the gap, with course evidence>)
Source: <real, verifiable reference | "no external source; agent's own explanation">
Label: extends beyond the course — the course does not teach this; the
       assignment/lesson does not require it. Use it to understand, not to
       submit as course work.

<the material: explanation / worked example / exercise / reference>
```

## Rules

- **Never fix the course's content silently.** Say "the course says X; the
  source says Y" and let the student (and teacher) see both.
- **A supplement to a graded assignment stays gated.** Supplementary *teaching*
  is fine; supplementary *answers* to graded tasks are not.
- **Keep supplements proportionate.** A two-line clarification beats a booklet.
  Only build what the gap needs.

## References

- `mapping-course-syllabus` — the gap source
- `explaining-concepts` — the teaching a supplement supports
- `creating-practice-exercises` — when the fill is practice, not prose
