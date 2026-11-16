import os
import unittest


class Ci(unittest.TestCase):
    def test_environment_ci_is_true_or_unset(self) -> None:
        self.assertIn(os.environ.get("CI", "unset"), ("true", "unset"))
