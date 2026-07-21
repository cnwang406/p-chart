import unittest

from update_helpers import (
    is_newer_release,
    normalize_build_number,
)


class UpdateHelpersTest(unittest.TestCase):
    def test_newer_release_checks_version_before_build(self) -> None:
        self.assertTrue(is_newer_release('v3.1', '0101', 'v3.0', '1231'))
        self.assertFalse(is_newer_release('v2.9', '9999', 'v3.0', '0001'))

    def test_newer_release_checks_build_for_same_version(self) -> None:
        self.assertTrue(is_newer_release('v3.0', '0717', 'v3.0', '0716'))
        self.assertFalse(is_newer_release('v3.0', '0716', 'v3.0', '0716'))
        self.assertFalse(is_newer_release('v3.0', '0715', 'v3.0', '0716'))

    def test_normalize_build_number_accepts_display_text(self) -> None:
        self.assertEqual(normalize_build_number('build 0716'), '0716')
        self.assertEqual(normalize_build_number(717), '0717')
        self.assertEqual(normalize_build_number(''), '')


if __name__ == '__main__':
    unittest.main()
