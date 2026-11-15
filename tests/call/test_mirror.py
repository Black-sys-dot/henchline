"""call/hooks/mirror.py: what may reach the phone from the terminal."""

import json
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
import mirror


class Secrets(unittest.TestCase):
    def test_common_shapes_are_hidden(self):
        self.assertEqual(mirror.hidden("my token=abc123xyz ok"), "my token=[hidden] ok")
        self.assertEqual(mirror.hidden("password: s3cret!"), "password: [hidden]")
        self.assertEqual(mirror.hidden("postgres://bob:hunter2@db/x"), "postgres://bob:[hidden]@db/x")
        self.assertEqual(mirror.hidden("key ghp_abcdefghijklmnopqrstuvwxyz0123 end"), "key [hidden] end")
        self.assertEqual(mirror.hidden("-----BEGIN RSA PRIVATE KEY-----\nAAA\n-----END RSA PRIVATE KEY-----"), "[hidden]")

    def test_ordinary_text_is_left_alone(self):
        for text in ("normal text", "see https://example.com/a:b", "the token is ready"):
            self.assertEqual(mirror.hidden(text), text)


class Reply(unittest.TestCase):
    def test_the_written_reply_since_the_last_prompt(self):
        lines = [
            {"type": "user", "message": {"content": "first prompt"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "old answer"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "second prompt"}]}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hm"},
                                                          {"type": "tool_use", "name": "Bash"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "output"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Part one."}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Part two."}]}},
        ]
        path = Path(sandbox.HOME) / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines))
        self.assertEqual(mirror.reply(path), "Part one.\n\nPart two.")


if __name__ == "__main__":
    unittest.main()
