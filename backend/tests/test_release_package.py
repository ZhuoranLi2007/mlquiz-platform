import gzip
import sqlite3
from pathlib import Path


def test_demo_package_keeps_historical_regrade_lineage(tmp_path):
    package = Path(__file__).resolve().parents[2] / "data" / "demo" / "mlquiz-course-demo.sqlite3.gz"
    database = tmp_path / "demo.sqlite3"
    database.write_bytes(gzip.decompress(package.read_bytes()))
    with sqlite3.connect(database) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from scoring_runs where status = 'completed'").fetchone()[0] > 0
        assert db.execute("select count(*) from answer_regrades").fetchone()[0] > 0
        assert db.execute("""
            select count(*) from answer_regrades r
            left join scoring_runs s on s.scoring_run_id = r.scoring_run_id
            where s.scoring_run_id is null
        """).fetchone()[0] == 0
        for table in ("session_tokens", "model_calls", "skill_runs", "jobs"):
            assert db.execute(f"select count(*) from {table}").fetchone()[0] == 0
