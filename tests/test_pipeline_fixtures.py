import json
from datetime import date

import pytest

from app.pipeline.run import review_all
from app.skills import SkillRegistry

EXPECTED = json.loads(open("fixtures/expected.json").read())


def test_all_fixtures_replay(client):
    reviews = review_all(client, SkillRegistry().get("default"), run_date=date(2026, 11, 6))
    got = {r["expense_id"]: r for r in reviews}
    assert len(got) == len(EXPECTED) == 14
    for exp in EXPECTED:
        r = got[exp["expense_id"]]
        actual = {k: r[k] for k in exp if k != "expense_id"}
        assert actual == {k: v for k, v in exp.items() if k != "expense_id"}, exp["expense_id"]
