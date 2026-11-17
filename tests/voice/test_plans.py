"""voice/plans.py: a plan file's steps, and which file belongs to a project."""

import tempfile
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401
from voice import plans

SAMPLE = """---
name: Phone plan
overview: Show the steps on the phone.
todos:
  - id: one
    content: "Read the file"
    status: completed
  - id: two
    content: "Draw the steps"
    status: in_progress
  - id: three
    content: A button
    status: pending
isProject: false
---

# Phone plan

```mermaid
flowchart LR
  a --> b
```
"""


class Parsed(unittest.TestCase):
    def test_the_steps_and_how_many_are_done(self):
        got = plans.parse(SAMPLE)
        self.assertEqual(got["name"], "Phone plan")
        self.assertEqual(got["done"], 1)
        self.assertEqual(got["total"], 3)
        self.assertEqual(got["todos"][1]["status"], "in_progress")
        self.assertIn("mermaid", got["body"])

    def test_a_cursor_file_takes_its_title_from_the_heading(self):
        text = """---
todos:
  - id: one
    content: "Read the file"
    status: pending
isProject: false
---
# Email into the list
"""
        got = plans.parse(text)
        self.assertEqual(got["name"], "Email into the list")
        self.assertEqual(got["total"], 1)

    def test_a_plan_that_names_the_project_is_the_one(self):
        root = Path(tempfile.mkdtemp())
        folder = root / "plans"
        folder.mkdir()
        (folder / "other.plan.md").write_text("---\nname: Other\ntodos:\n  - id: a\n    content: no\n    status: pending\n---\n")
        (folder / "mine.plan.md").write_text(SAMPLE.replace("Phone plan", "henchline work"))
        project = root / "henchline"
        project.mkdir()
        import voice.plans as mod
        original = mod._places
        mod._places = lambda _project: [folder]
        try:
            got = plans.find(project)
        finally:
            mod._places = original
        self.assertEqual(got["name"], "henchline work")
        self.assertTrue(got["file"].endswith("mine.plan.md"))


if __name__ == "__main__":
    unittest.main()
