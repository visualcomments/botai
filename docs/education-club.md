# Open Education Club catalog MCP (reference)

The [Open Education Club](https://sourcecraft.dev/open-education-club-by-yandex/open-education-club-by-yandex)
is a library of open courses from Yandex Cloud and SourceCraft: lectures, labs,
and assignments by professors of leading universities. The catalog repository
ships an MCP server (`mcp/catalog-mcp.py`) that exposes the catalog to the
agent, so a co-learning session can start from the catalog: the agent shows the
student the courses, reads a course's README, fetches its materials into the
workspace, and begins going through the course together with the student.

This document is the setup reference for the agent's catalog workflow, run by
the `starting-course-from-education-club` skill and the `/education-club`
command.

## What the MCP exposes

| Tool | Purpose |
| --- | --- |
| `list_courses()` | The whole catalog: slug, title, university, authors. |
| `get_course(<slug>)` | Course metadata + the course repo's README (audience, prerequisites, format). |
| `fetch_course(<slug>, courses)` | Git-clones the course repo into `courses/<slug>/` for immediate use. |

The catalog is public — no Yandex account is needed to read or fetch course
content.

## Setup

The MCP server lives in the catalog repository, which is a separate checkout
from this botai project. Two steps:

1. Clone the catalog (or point `EDUCATION_CLUB_CATALOG` at an existing
   checkout):

   ```bash
   git clone https://git.sourcecraft.dev/open-education-club-by-yandex/open-education-club-by-yandex.git
   ```

2. Install its Python dependency and register the server in `opencode.json`:

   ```bash
   python3 -m pip install -r <checkout>/mcp/requirements.txt
   ```

   ```json
   {
     "mcp": {
       "education-club": { "type": "local", "command": ["python3", "<checkout>/mcp/catalog-mcp.py"], "enabled": true }
     }
   }
   ```

   Restart opencode after editing the config.

## Using it

1. `/education-club` (or tell the agent to browse the catalog).
2. Agent lists courses, shortlists with the student, reads the READMEs.
3. Consent gate (level, feedback delivery, graded vs practice) is recorded.
4. `fetch_course` pulls the chosen course into `courses/<slug>/`.
5. Syllabus is mapped and co-learning starts.

## Policy notes

- The catalog and course repos are the source of truth; the agent never invents
  courses or prerequisites that are not there.
- Some courses are still in progress (lectures/labs TBA). The agent must say so
  and mark any supplement it would add.
- The graded-vs-practice split and the "no ready answers" rule from AGENTS.md
  apply to catalog courses like to any other.
- If the chosen course is also an open-source project (top-papers style), the
  `onboarding-open-source-contributors` skill / `/contribute` command is an
  alternative to plain co-learning.
