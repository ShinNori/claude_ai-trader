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
    path = Path(__file__).resolve().parents[2]/'common/synth_data.py'
    spec = importlib.util.spec_from_file_location('shared_synthetic',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tables = module.build(seed=seed)
    with db.connect(home) as con:
        con.execute('BEGIN')
        try:
            for name, frame in tables.items():
                con.register('incoming',frame)
                con.execute(f'DELETE FROM {name}')
                con.execute(f'INSERT INTO {name} SELECT * FROM incoming')
                con.unregister('incoming')
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('data_mode','synthetic')")
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('seed',?)",[str(seed)])
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise

def run_signals(home, strategy, as_of):
    with db.connect(home) as con:
        return [asdict(c) for c in get_strategy(strategy).generate(as_of,con)]

def run_backtest(home, strategy, start, end, out_dir):
    return run(home,strategy,start,end,out_dir)
