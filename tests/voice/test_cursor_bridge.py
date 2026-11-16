import unittest

from voice.desktop import cursor_bridge


class ProjectNames(unittest.TestCase):
    """Cursor's project folder names, as seen under ~/.cursor/projects (2026-11-16): a change in its scheme must
    show up as a refused delivery, so these pin the assumption."""

    def test_dashed_path(self):
        self.assertEqual(cursor_bridge.project_of("/home/shekharu/colcon_ws/src/smarc2"),
                         "home-shekharu-colcon-ws-src-smarc2")

    def test_dots_and_doubled_separators(self):
        self.assertEqual(cursor_bridge.project_of("//home/u/my.project__x/"), "home-u-my-project-x")

    def test_chat_of(self):
        chat = "1e6e96a3-90f8-476a-9fe9-d2be71fc2053"
        self.assertEqual(cursor_bridge.chat_of("cursor-" + chat), chat)
        self.assertEqual(cursor_bridge.chat_of("cursor-_smarc2"), "")


if __name__ == "__main__":
    unittest.main()
