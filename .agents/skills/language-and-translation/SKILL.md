---
name: language-and-translation
description: Present all course material in Russian — Russian-language terminology instead of English or other foreign words, and Russian translations of quotations from sources. Covers choosing a Russian term when the literature is English-only, the mandatory original-plus-translation quotation format, what must never be translated (identifiers, file paths, commands, proper names in citations), and how to keep translations honest rather than paraphrased. Use whenever explaining course material, quoting a source, producing session material or assignment briefs, or writing progress notes — in any course whose language of instruction is Russian.
verified: 2026-09-12
---

# Language and translation

The course is taught in Russian. Material written for the student is written in
Russian — not in English with Russian glue around it. Two obligations follow,
and both are checkable:

1. **Russian terminology.** A term is given in Russian, not borrowed in Latin
   script.
2. **Russian quotations.** A quotation from a foreign-language source appears
   with a Russian translation, next to the original.

"Everyone in this field says *deep learning* anyway" is not an exception. This
skill is about what the student reads, and the student reads Russian.

## Russian terminology

Prefer the established Russian term:

| Do not write | Write |
|---|---|
| workflow | рабочий процесс, порядок работы |
| research question | исследовательский вопрос |
| peer review | рецензирование (научное) |
| deadline | срок сдачи |
| feedback | обратная связь |
| scope | охват, рамки работы |
| roadmap | план, дорожная карта |
| learning outcomes | результаты обучения |
| dataset | набор данных |
| machine learning | машинное обучение |
| case study | разбор случая, кейс (по устоявшейся практике) |
| benchmark | эталонное сравнение |

Some loanwords are **normalized Russian** and are the correct term —
*компьютер*, *алгоритм*, *программа*, *интерфейс*, *файл*, *сервер*. The rule
is not "no loanwords"; it is **no untranslated foreign words**. A word that
Russian has adopted and now declines (*алгоритм*, *алгоритма*, *алгоритмы*) is
a Russian word. A word in Latin script dropped into a Russian sentence
(*benchmark*, *workflow*) is not.

### When only the English term exists

Often the Russian literature has no settled term — the concept arrived recently
and only English names it. Then:

1. Give the Russian explanation **first**, in your own words, so the idea is
   understandable without the foreign word.
2. Give the foreign term once, in parentheses, so the student can find the
   literature: «состязательная проверка (adversarial evaluation)».
3. Thereafter use the Russian description, not the foreign term.

Never leave the foreign term as the only name for the concept. "It has no
Russian equivalent" usually means "I did not look", and a student who cannot
read English is then locked out of the material.

## Quotations: original plus translation

The required format for any quotation from a non-Russian source:

> «the original text, verbatim»
> — перевод: русский перевод
> `источник · фрагмент #N`

Rules that make this honest:

- **The original is mandatory** and stays **verbatim**. A quotation is evidence;
  a translation is not. `make verify` checks the original against the corpus —
  a translated quotation cannot be verified and therefore cannot be cited.
- **Mark the translation as a translation.** It is your work, not the author's
  words. It must never be presented as a quotation from the source.
- **Translate accurately, not beautifully.** Preserve the author's hedging
  («по-видимому», «как представляется»), their uncertainty, and their
  terminology. Smoothing a cautious claim into a confident one is a
  distortion, and it is the most common way a translation misleads.
- **Do not silently drop content.** If a phrase resists translation, translate
  as far as it goes and add «[непереводимая игра слов]» / «[…]» rather than
  quietly omitting it.
- **Keep the coordinate** (`файл · фрагмент #N`) attached to the original.
  Translations inherit their citation from the original; they do not get their
  own.

For a Russian-language source, no translation is needed — quote it as it is.

### What is never translated

These are identifiers, not prose. Translating or transliterating them breaks
the tooling and the citation:

- file paths, file names, chunk coordinates (`file · фрагмент #N`);
- commands, flags, environment variables (`COURSE_CORPUS_ROOT`, `make verify`);
- code, JSON keys, API and tool names;
- bibliographic identifiers — DOI, ISBN, ISSN, arXiv id;
- authors' names and work titles **inside a citation** (the citation points at
  a real record; the Russian rendering may follow in parentheses after it);
- the contents of a quotation's original line.

## Where this applies

Everywhere the student reads:

- lesson material and explanations (`explaining-concepts`);
- session material and assignment briefs (`session_material.py`,
  `assignment_brief.py`);
- feedback on an attempt (`giving-feedback`);
- supplements (`providing-supplementary-material`);
- progress records and reports (`maintaining-course-progress`,
  `reporting-learning-progress`).

The corpus itself is **not** translated: source texts stay in their original
language. Translation happens when material is presented, never in the stored
corpus — rewriting corpus files would invalidate the index, the quote
verification, and the provenance record.

## Self-check before delivering

1. Is there any Latin-script prose word in the Russian text? Replace it, or
   gloss it as described above.
2. Does every foreign quotation carry both the verbatim original and a marked
   translation?
3. Did any translation quietly drop a hedge, a qualifier, or a clause?
4. Are identifiers left untranslated?
5. Would a student who reads only Russian understand the whole text?

A "no" to any of these is a defect to fix before the material reaches the
student — not a stylistic preference.
