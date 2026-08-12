---
description: Detect gaps in the course material and prepare labeled, sourced supplements.
agent: botai
---

Detect gaps in the course material for: $ARGUMENTS (module or topic).

1. Read the relevant course material and find what it does not cover, covers
   unclearly, or covers incorrectly. Use the course text as evidence.
2. Run the `supplementer` agent using `providing-supplementary-material`: fill
   the gaps with explanations, examples, practice, or external references.
3. Every supplement must be labeled as extending beyond the course and cite a
   real source. Never fabricate citations.
4. If a gap affects a graded assignment, supplementary teaching is fine but
   supplementary answers are not.
5. Record the gaps and supplements in the progress record.

Do not silently rewrite or replace the course's own text.
