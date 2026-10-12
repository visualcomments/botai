---
name: reviewing-github-assignments
description: Review a student's GitHub Classroom submission from what they provide - the diff, the test results, the pull-request description - produce feedback in the giving-feedback shape, state coverage as a pass ratio with its denominator, and leave the commit, the push, and the pull request to the student. Use when a student pastes a diff or a PR body for review, when a Classroom assignment is due, or when a teacher asks the agent to look at a submission.
verified: 2026-09-30
---

# Reviewing GitHub Assignments

The review reads what the student brings. There is no network in this harness:
`integrations.GitHubClassroomClient` parses a Classroom URL without fetching it,
builds a Markdown report body, and renders an Actions workflow as text. It never
clones, never pushes, never calls the GitHub API -
`remote_calls_disabled()` is always `True`, with the reason, so `doctor` can
report honestly.

## When to Use

- A student pastes a diff, a PR description, or test output for review
- A GitHub Classroom assignment is due and the student wants feedback
- A teacher asks the agent to look at a submission

## When NOT to Use

- **Producing the work** - that is `breaking-down-assignments`, and the
  ceiling applies
- **Committing, pushing, or opening the PR** - the student does that; see
  "The student owns the contribution"
- **The student has not written anything yet** - there is nothing to review

## What the agent reads, and where it comes from

1. **The diff the student pastes** (`git diff` output, a PR body, or files).
2. **The test results the student pastes.** The harness does not run tests.
3. **The assignment reference**, parsed by `parse_assignment_ref(url)` into
   `{owner, repo, assignment}`. It refuses a non-GitHub or non-Classroom URL
   with a Russian message, and it **parses only - it never fetches the URL**.
4. Optionally, `render_action_yaml()` to render the Actions workflow text for
   the student to use. The workflow references `${{ secrets.GITHUB_TOKEN }}` -
   no secret is invented. If an output path is given it must resolve inside the
   workspace (`paths.ensure_within`); otherwise nothing is written at all.

If the student has not provided a diff or test output, say so and ask for it.
Do not reconstruct a submission from a repo description.

## The review

`build_review_report(...)` produces a Markdown PR-comment body. The shape is
`giving-feedback`:

```
What is right:
  <specific correct parts>

What needs work:
  <specific gap>  ->  how to find and fix it yourself: <strategy>

Coverage: 3 из 5 тестов проходят.
```

Rules carried from `giving-feedback`:

- Every point cites the attempt. A line in the diff, a test name, an output
  line - not a general impression.
- Give the strategy, not the fix, on a graded task.
- One gap at a time, ordered.

## State coverage with its denominator

`build_review_report` states the score as a **test-pass ratio with the
denominator shown** - «3 из 5», never «60%» alone, never a letter, never a
number out of a scale.

The denominator is the whole point: 3 из 5 with five submitted tests is a
different situation from 3 из 5 with three submitted and two never written.
Always report the number of tests that existed in the assignment versus the
number the student supplied, and say when the student supplied fewer.

**No grade is claimed.** A grade is a course judgment from the accepted
contract; a comment body that asserted one would be the harness inventing a
mark. If the teacher asks for a grade, point at the rubric and the contract.

## The student owns the contribution

From AGENTS.md's open-source contribution integrity, and not negotiable:

- The student makes the commit, runs `git push`, and opens the pull request.
  The agent never does any of these, and never claims authorship.
- No fabricated commits, ids, hashes, review states, or course revisions. If the
  student has not run the tests, the report says so rather than estimating.
- Disclose AI assistance where the project requires it. Never present agent
  output as the student's untested work.
- The project's own `CONTRIBUTING.md` is authoritative; the agent teaches the
  student to read it, not to work around it.
- Secrets, closed files, and unlicensed material never go into a submission.

When the student asks the agent to commit on their behalf, refuse and explain
that the commit is theirs; offer to review the staged diff instead.

## The ceiling applies here too

A review of a graded assignment is HINT and EXAMPLE only:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level EXAMPLE
```

`policy.py` decides. A Classrooms assignment is graded unless the accepted
contract says otherwise, and «это же просто мой репозиторий» is not a
reclassification.

## Limitations

- **No network.** No clone, no fetch, no API call, no push. `remote_calls_disabled()`
  is `True` by design. The agent reviews only what the student pastes.
- No tests are run and no build is invoked. Test results are the student's
  reported output, and a report based on unrun tests says exactly that.
- `render_action_yaml` returns workflow **text**. Writing it to a path requires
  an in-workspace path; the harness will not write outside it, and it invents no
  token.
- `parse_assignment_ref` parses a URL string. It does not verify that the repo,
  the branch, or the assignment exists.
- The report is a comment body a human or the student can read and adapt. It is
  not posted anywhere.

## References

- `integrations.py` - `GitHubClassroomClient`: `parse_assignment_ref`,
  `build_review_report`, `render_action_yaml`, `remote_calls_disabled`
- `giving-feedback` - the review shape and the invariants before it leaves draft
- `onboarding-open-source-contributors` - the full contributor path
- `reviewing-github-assignments` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for the assignment before reviewing it, and read the
   rubric rather than inventing a standard where the rubric is silent.
2. Respect the recorded delivery preference; the ceiling applies to a Classroom
   assignment as it does to any graded task.
3. Record: the assignment ref, what the student supplied (diff, tests, or
   neither), the pass ratio with its denominator, and each gap named.
4. Never record a grade, and never record the agent's suggested code as the
   student's demonstration.
5. Deliver the review in Russian (`language-and-translation`), and carry
   `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`integrations.py`, the Classroom flow, or the contribution-integrity rules
change. Added this revision: the parse-only/no-network reality, the
denominator-stated coverage rule, and the explicit statement that commit, push,
and pull request belong to the student.
