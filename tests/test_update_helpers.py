import tempfile
from pathlib import Path
import shutil
import unittest

from update_helpers import (
    copy_update_files,
    is_newer_release,
    normalize_build_number,
    stage_update_files,
    windows_update_script,
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

    def test_stage_update_files_copies_before_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as sourceText:
            sourceDirectory = Path(sourceText)
            (sourceDirectory / 'p-chart.exe').write_text('new', encoding='utf-8')
            (sourceDirectory / '_internal').mkdir()
            (sourceDirectory / '_internal' / 'library.dll').write_text('dll', encoding='utf-8')

            stageDirectory = stage_update_files(sourceDirectory)
            try:
                self.assertEqual((stageDirectory / 'p-chart.exe').read_text(), 'new')
                self.assertEqual(
                    (stageDirectory / '_internal' / 'library.dll').read_text(),
                    'dll',
                )
            finally:
                shutil.rmtree(stageDirectory, ignore_errors=True)

    def test_copy_update_files_reports_deterministic_progress(self) -> None:
        with (
            tempfile.TemporaryDirectory() as sourceText,
            tempfile.TemporaryDirectory() as targetText,
        ):
            sourceDirectory = Path(sourceText)
            targetDirectory = Path(targetText)
            (sourceDirectory / 'z-last.txt').write_text('z', encoding='utf-8')
            (sourceDirectory / 'A-first.txt').write_text('a', encoding='utf-8')
            progressUpdates = []

            copy_update_files(
                sourceDirectory,
                targetDirectory,
                lambda current, total, name: progressUpdates.append(
                    (current, total, name)
                ),
            )

            self.assertEqual(
                progressUpdates,
                [
                    (1, 2, 'A-first.txt'),
                    (2, 2, 'z-last.txt'),
                ],
            )
            self.assertEqual((targetDirectory / 'A-first.txt').read_text(), 'a')
            self.assertEqual((targetDirectory / 'z-last.txt').read_text(), 'z')

    def test_windows_updater_waits_before_copy_and_restarts(self) -> None:
        script = windows_update_script(
            Path(r'C:\Temp\stage'),
            Path(r'C:\Apps\p-chart'),
            Path(r'C:\Apps\p-chart\p-chart.exe'),
            1234,
        )
        self.assertLess(script.index(':wait_for_app'), script.index('robocopy'))
        self.assertIn('tasklist /FI "PID eq %UPDATE_PID%"', script)
        self.assertIn('if %UPDATE_RESULT% GEQ 8 goto update_failed', script)
        self.assertIn('start "" "%UPDATE_EXE%"', script)


if __name__ == '__main__':
    unittest.main()
