"""voice/speech/nemotron.py: words and their times from the model's pieces, and hint words not repeated."""

import json
import tempfile
import unittest
from pathlib import Path

from tests import sandbox  # noqa: F401  (first: a throwaway HOME)
from voice.speech import nemotron


class Words(unittest.TestCase):
    def test_pieces_join_into_spaced_words(self):
        words = nemotron.words_of([" Wh", "at", "'", "s", " the", " G", "P", "U"],
                                  [1.0, 1.0, 1.0, 1.0, 1.2, 1.3, 1.4, 1.5], [1.0, 1.0, 1.0, 1.2, 1.3, 1.4, 1.5, 1.6])
        self.assertEqual([w for _, _, w in words], [" What's", " the", " GPU"])
        self.assertEqual(words[2][:2], (1.3, 1.6))
        self.assertEqual("".join(w for _, _, w in words).strip(), "What's the GPU")  # as tone.annotate checks


class Tidy(unittest.TestCase):
    def test_a_hint_word_repeated_in_silence_is_said_once(self):
        self.assertEqual(nemotron.tidy("Claude Claude Claude, can you check?", ["Claude"]), "Claude, can you check?")

    def test_other_repeats_stay(self):
        self.assertEqual(nemotron.tidy("very very good", ["Claude"]), "very very good")


class Vocab(unittest.TestCase):
    def test_pieces_scored_by_rank_without_specials(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "t.json").write_text(json.dumps({"model": {"vocab": {"<unk>": 0, "▁t": 1, "er": 2}}}))
        nemotron.build_vocab(folder / "t.json", folder / "bpe.vocab")
        self.assertEqual((folder / "bpe.vocab").read_text(), "▁t\t-1.0\ner\t-2.0\n")


if __name__ == "__main__":
    unittest.main()
