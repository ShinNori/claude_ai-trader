"""The result lock serializes distinct Python processes, not just threads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SCRIPT = r'''
import sys, time
from pathlib import Path
import pandas as pd
from aitrader.backtest_artifacts import publish_backtest_artifacts
folder, ready, release, hold = map(Path, sys.argv[1:])
if str(hold) == 'hold':
    original = pd.DataFrame.to_csv
    def pause(frame, path, *args, **kwargs):
        ready.write_text('locked')
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('barrier timeout')
            time.sleep(.025)
        return original(frame, path, *args, **kwargs)
    pd.DataFrame.to_csv = pause
try:
    publish_backtest_artifacts(folder, pd.DataFrame({'pnl':[0]}),
                              pd.DataFrame({'equity':[100]}),
                              {'yearly':{},'version':'process-test'})
except ValueError:
    sys.exit(7)
'''


def test_result_lock_is_exclusive_across_processes(tmp_path):
    script = tmp_path/'writer.py'
    script.write_text(SCRIPT, encoding='utf-8')
    folder, ready, release = tmp_path/'result', tmp_path/'ready', tmp_path/'release'
    env = dict(os.environ)
    package = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(package) + os.pathsep + env.get('PYTHONPATH', '')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [sys.executable, str(script), str(folder), str(ready), str(release)]
    first = subprocess.Popen(command+['hold'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.025)
        assert ready.exists(), 'First writer did not reach the staging barrier'
        second = subprocess.run(command+['no-hold'], env=env, capture_output=True, timeout=10)
        assert second.returncode == 7, second.stderr.decode(errors='replace')
        assert not folder.exists()
        assert (tmp_path/'.result.publish.lock').is_file()
    finally:
        release.write_text('continue', encoding='utf-8')
        try:
            stdout, stderr = first.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            first.kill()
            first.communicate()
            raise
    assert first.returncode == 0, stderr.decode(errors='replace')
    assert json.loads((folder/'summary.json').read_text(encoding='utf-8'))['version'] == 'process-test'
    assert {path.name for path in folder.iterdir()} == {
        'summary.json', 'report.html', 'trades.csv', 'equity.csv'}
    assert not (tmp_path/'.result.publish.lock').exists()
