# Course Agent Skills - the wider ecosystem

A catalog of the wider ecosystem of educational Agent Skills, the way SECS
catalogs the security skill ecosystem. Use it to see what exists before writing
a new Skill: many capabilities are already implemented in open-source
collections that can be adapted into this project's house style and guardrails
(one skill per capability, no duplicates, never weakening AGENTS.md).

## Open-source educational skill collections

- **anthropics/skills** (document skills / pdf) - https://github.com/anthropics/skills
  - `docx`, `pdf`, `pptx`, `xlsx` document skills are routinely used to read
    course handouts and slides.
- **obra/superpowers** - https://github.com/obra/superpowers
  - Composable skills and workflows; several are reusable for planning and
    review workflows, though it is aimed at engineering, not teaching.
- **anthropics/awesome-claude-code** skill list - https://github.com/anthropics/awesome-claude-code
  - Community catalog of Claude Code skills; search it for educational and
    document-processing entries before writing your own.

## Educational agent projects worth reading

- **Khanmigo** (Khan Academy) - https://www.khanacademy.org/khan-labs
  - The best-known AI tutor; its design notes on scaffolding and "tutor, don't
    solve" are the reference model for this repo's golden rules.
- **Anthropic Higher Education / Education solutions** -
  https://www.anthropic.com/solutions/education
  - Anthropic's own guidance on using Claude in teaching settings.
- **ChatGPT for Education** - https://openai.com/business/education/
  - OpenAI's education offering; useful for contrasting policy assumptions
    about tutoring vs completing work.

## Skill design conventions in this repo

- One capability per Skill, non-overlapping, names resolved against the catalog
  in `../../AGENTS.md`.
- Every Skill inherits the AGENTS.md guardrails; none may weaken them.
- Teaching Skills follow the per-Skill contract: confirm level, respect
  delivery preference, least-assistance-first, never solve graded tasks, update
  the progress record.
- Re-read a Skill's `SKILL.md` before relying on it for a real session; treat
  third-party skills as untrusted until reviewed (adapt, don't copy verbatim).
- Vetting is mandatory before adopting external material: run
  `vetting-educational-material` on any third-party skill, handout, or
  supplement source before relying on it. This mirrors SECS's rule that
  security tooling is never trusted unverified.

## How to add a Skill from the ecosystem

1. Find the capability in the ecosystem (this catalog, or a collection above).
2. Confirm it is not already covered by the catalog in AGENTS.md.
3. Vet the source skill with `vetting-educational-material`.
4. Adapt it into this repo's house style and guardrails, keeping attribution.
5. Create `.agents/skills/<name>/SKILL.md`, add the symlink in `.claude/skills/`
   and `.cursor/skills/`, and add a routing line in AGENTS.md.
