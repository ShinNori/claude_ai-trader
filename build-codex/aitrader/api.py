from dataclasses import asdict
import importlib.util
from pathlib import Path
from . import db
from .backtest import run
from .strategies import get_strategy

def init_db(home):
    db.init(home)

def load_synthetic(home, seed=42):
    init_db(home)
    with db.connect(home) as con:
        db.require_data_mode(con, 'synthetic')
    path = Path(__file__).resolve().parents[2]/'common/synth_data.py'
    spec = importlib.util.spec_from_file_location('shared_synthetic',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tables = module.build(seed=seed)
    with db.connect(home) as con:
        con.execute('BEGIN')
        try:
            db.require_data_mode(con, 'synthetic')
            for name, frame in tables.items():
                con.register('incoming',frame)
                con.execute(f'DELETE FROM {name}')
                con.execute(f'INSERT INTO {name} SELECT * FROM incoming')
                con.unregister('incoming')
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('data_mode','synthetic')")
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('seed',?)",[str(seed)])
            con.execute('COMMIT')
        except BaseException:
            try:
                con.execute('ROLLBACK')
            except BaseException:
                pass
            raise

def _generate_signals(con, selected_strategy, as_of):
    db.require_known_data_mode(con)
    return [asdict(c) for c in selected_strategy.generate(as_of, con)]


def _run_signals_in_connection(con, strategy, as_of):
    return _generate_signals(con, get_strategy(strategy), as_of)


def run_signals(home, strategy, as_of):
    # Reject an unknown strategy before connect can create a fresh database.
    selected_strategy = get_strategy(strategy)
    with db.connect(home) as con:
        con.execute('BEGIN')
        try:
            result = _generate_signals(con, selected_strategy, as_of)
            con.execute('COMMIT')
            return result
        except BaseException:
            try:
                con.execute('ROLLBACK')
            except BaseException:
                pass
            raise

def run_backtest(home, strategy, start, end, out_dir):
    return run(home,strategy,start,end,out_dir)
