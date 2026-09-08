from pathlib import Path
import yaml
from .base import Candidate

class MarginBucketLong:
    name = 'margin_bucket_long'
    rebalance = 'weekly_mon'

    def __init__(self):
        path = Path(__file__).resolve().parents[2] / 'config/strategies.yaml'
        self.config = yaml.safe_load(path.read_text(encoding='utf-8'))[self.name]
        self.version = self.config['version']
        self.holding_days = int(self.config['holding_days'])

    def ranked(self, as_of, db):
        # Restrict both market observations and publication dates before ranking.
        return db.execute('''
        WITH days AS (SELECT DISTINCT date FROM prices_daily WHERE date<=? ORDER BY date DESC LIMIT 60),
        liquid AS (SELECT p.code FROM prices_daily p JOIN days d USING(date)
          GROUP BY p.code HAVING count(*)=60 AND avg(turnover)>=?),
        m AS (SELECT *, row_number() OVER(PARTITION BY code ORDER BY date DESC) rn
          FROM margin_weekly WHERE publish_date<=? AND date<=?),
        factors AS (SELECT code,
          max(CASE WHEN rn=1 THEN long_balance END) latest,
          max(CASE WHEN rn=5 THEN long_balance END) baseline FROM m WHERE rn<=5 GROUP BY code)
        SELECT f.code, f.latest/f.baseline-1 AS chg FROM factors f JOIN liquid l USING(code)
        JOIN listed u USING(code)
        WHERE u.market IN ('prime','standard') AND u.listed_date<=CAST(? AS DATE)-INTERVAL 1 YEAR
          AND f.baseline>0 AND f.latest>=0
        ORDER BY chg, f.code
        ''', [as_of, self.config['min_turnover'], as_of, as_of, as_of]).fetchall()

    def universe(self, as_of, db):
        return [code for code, _ in self.ranked(as_of, db)]

    def generate(self, as_of, db):
        rows = self.ranked(as_of, db)
        selected = rows[:min(len(rows)//5, int(self.config['max_candidates']))]
        return [Candidate(str(code), 'BUY', 1/len(selected), -float(chg),
                          f'信用買残4週変化率 {chg:.2%}（合成/研究データの可能性あり）',
                          self.name, self.version, as_of, self.holding_days,
                          float(self.config['limit_pct'])) for code, chg in selected]
