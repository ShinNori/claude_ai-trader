"""Deployment regressions use temporary files and no actual agent or task."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import codex_engine as h


class StatusReplacementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='handoff-status-review-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / h.STATUS_FILE
        self.path.parent.mkdir(parents=True)
        self.path.write_text('previous observation', encoding='utf-8')

    def sharing_error(self, code=32):
        error = PermissionError('synthetic Windows sharing conflict')
        error.winerror = code
        return error

    def test_status_recovers_after_temporary_windows_conflict(self):
        original_replace = h.os.replace
        attempts = []

        def replace(source, destination):
            attempts.append(source)
            self.assertEqual(self.path.read_text(encoding='utf-8'), 'previous observation')
            if len(attempts) < 3:
                raise self.sharing_error(5)
            original_replace(source, destination)

        with patch.object(h.os, 'replace', side_effect=replace), patch.object(h.time, 'sleep'):
            h.write_status(self.root, {'agent': 'codex', 'phase': 'observed'})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['phase'], 'observed')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_persistent_status_conflict_stops_without_corrupting_old_file(self):
        with patch.object(h.os, 'replace', side_effect=self.sharing_error()) as replace, \
                patch.object(h.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                h.write_status(self.root, {'agent': 'codex'})
        self.assertEqual(replace.call_count, 6)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list), 1.55)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'previous observation')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_unrelated_file_error_is_not_retried(self):
        with patch.object(h.os, 'replace', side_effect=self.sharing_error(87)) as replace, \
                patch.object(h.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                h.write_status(self.root, {'agent': 'codex'})
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()

    def test_protocol_file_writes_keep_immediate_failure(self):
        with patch.object(h.os, 'replace', side_effect=self.sharing_error()) as replace, \
                patch.object(h.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                h.atomic_text(self.path, 'new protocol content')
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'previous observation')

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_windows_open_reader_delays_replace_until_reader_closes(self):
        reader = self.path.open('r', encoding='utf-8')
        self.addCleanup(reader.close)

        def release_reader(delay):
            self.assertFalse(reader.closed)
            self.assertEqual(reader.read(), 'previous observation')
            reader.close()

        with patch.object(h.time, 'sleep', side_effect=release_reader) as sleep:
            h.write_status(self.root, {'agent': 'codex', 'phase': 'observed'})
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['phase'], 'observed')


if __name__ == '__main__':
    unittest.main()
