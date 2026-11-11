import os
import unittest


class Ci(unittest.TestCase):
    def test_environment_outside_ci_passes(self) -> None:
        self.assertNotEqual(os.environ.get("CI"), "true")
