import unittest

from lab.parser import parse_lines


class ParseLines(unittest.TestCase):
    def test_parse_lines_text_with_blank_lines_returns_the_non_empty_lines(self) -> None:
        self.assertEqual(parse_lines("a\n\nb\n"), ["a", "b"])

    def test_parse_lines_windows_line_ends_drops_carriage_returns(self) -> None:
        self.assertEqual(parse_lines("a\r\nb\r\n"), ["a", "b"])

    def test_parse_lines_comment_line_is_skipped(self) -> None:
        self.assertEqual(parse_lines("# note\na\n"), ["a"])
