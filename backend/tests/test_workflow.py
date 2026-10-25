import json
import pytest
from sqlalchemy import select
from app.db import Session, Analysis, Run, Step
from app.engine import prepare, run_experiment, Executor, steps_for
from app.worker import recover
from app.providers import ProviderError
from app.config import settings
from .fixtures import PROFILE, FakeProviders, CompetitiveProviders


async def prepared(client, providers):
    r = client.post("/api/v1/analyses", json=PROFILE.model_dump())
    assert r.status_code == 202
    ident = r.json()["id"]
    await prepare(ident, providers)
    a = client.get(f"/api/v1/analyses/{ident}").json()
    assert a["status"] == "awaiting_confirmation", a
    return a


def start(client, a, key="test-idempotent"):
    r = client.post(f"/api/v1/analyses/{a['id']}/runs", json={"plan_version": a["plan"]["version"]}, headers={"Idempotency-Key":key})
    assert r.status_code == 202, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_complete_persisted_workflow_and_export(client):
    providers = FakeProviders()
    a = await prepared(client, providers)
    ident = start(client, a)
    assert ident == start(client, a)
    await run_experiment(ident, providers)
    report = client.get(f"/api/v1/runs/{ident}/report").json()
    assert report["status"] == "completed", report
    assert report["metrics"]["coverage"]["numerator"] == 24
    assert report["metrics"]["recommendation"]["numerator"] == 24
    assert report["calls"]["search"] == 27  # 3 discovery + 24 experiments
    assert report["metrics"]["source_count"] == 1
    searches_before = len(providers.searches)
    with Session.begin() as db:
        db.get(Run, ident).status = "running"
    recover()
    await run_experiment(ident, providers)
    assert len(providers.searches) == searches_before
    exported = client.get(f"/api/v1/runs/{ident}/export")
    assert exported.status_code == 200
    assert len(exported.json()["audit_steps"]) >= 72
    assert "test-model-key" not in exported.text
    assert "test-search-key" not in exported.text
    rec_inputs = [json.loads(m[1]["content"]) for m in providers.messages if "独立推荐" in m[0]["content"]]
    assert rec_inputs and all(set(m)=={"question","evidence"} for m in rec_inputs)


@pytest.mark.asyncio
async def test_empty_results_are_valid_abstentions(client):
    providers = FakeProviders(empty=True)
    a = await prepared(client, providers)
    ident = start(client, a)
    await run_experiment(ident, providers)
    r = client.get(f"/api/v1/runs/{ident}/report").json()
    assert r["status"] == "completed"
    assert r["metrics"]["coverage"]["denominator"] == 24
    assert r["metrics"]["recommendation"]["denominator"] == 24
    assert r["metrics"]["recommendation"]["numerator"] == 0


@pytest.mark.asyncio
async def test_cancelled_queue_does_not_call_provider(client):
    providers = FakeProviders()
    a = await prepared(client, providers)
    ident = start(client, a)
    before = len(providers.searches)
    client.post(f"/api/v1/runs/{ident}/cancel")
    await run_experiment(ident, providers)
    assert len(providers.searches) == before
    assert client.get(f"/api/v1/runs/{ident}").json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_version_check_and_frozen_plan(client):
    a = await prepared(client, FakeProviders())
    patch = {"expected_version": 99, "profile": a["profile"], "plan": a["plan"], "accepted_suggestions": []}
    assert client.patch(f"/api/v1/analyses/{a['id']}/plan", json=patch).status_code == 409
    patch["expected_version"] = a["plan"]["version"]
    edited = client.patch(f"/api/v1/analyses/{a['id']}/plan", json=patch)
    assert edited.status_code == 200
    a = edited.json()
    start(client, a)
    patch["expected_version"] = a["plan"]["version"]
    assert client.patch(f"/api/v1/analyses/{a['id']}/plan", json=patch).status_code == 409


@pytest.mark.asyncio
async def test_fatal_credentials_not_retried(client):
    class Broken(FakeProviders):
        async def search(self, query):
            self.searches.append(query)
            raise ProviderError("未授权", fatal=True)
    p = Broken()
    with Session.begin() as db:
        row = Analysis(profile=PROFILE.model_dump());db.add(row);db.flush();ident=row.id
    await prepare(ident, p)
    assert len(p.searches) == 1
    assert client.get(f"/api/v1/analyses/{ident}").json()["status"] == "failed"


@pytest.mark.asyncio
async def test_budget_includes_retry_and_is_persistent(client, monkeypatch):
    p = FakeProviders()
    with Session.begin() as db:
        row = Analysis(profile=PROFILE.model_dump(), search_calls=39);db.add(row);db.flush();ident=row.id
    ex = Executor(ident, Analysis, p)
    assert await ex.search("one", "深圳市拌饭")
    assert await ex.search("two", "深圳市午餐") is None
    with Session() as db:
        assert db.get(Analysis, ident).search_calls == 40
    assert len(p.searches) == 1
    assert steps_for(ident)["two"]["status"] == "failed"


def test_protected_endpoints_and_invalid_session(client):
    r = client.get("/api/v1/analyses", headers={"Authorization":"Bearer invalid"})
    assert r.status_code == 401
    assert client.get("/api/v1/health").status_code == 200


@pytest.mark.asyncio
async def test_competitor_research_and_evidence_linked_advice(client):
    providers = CompetitiveProviders()
    a = await prepared(client, providers)
    ident = start(client, a)
    await run_experiment(ident, providers)
    r = client.get(f"/api/v1/runs/{ident}/report").json()
    assert r["status"] == "completed", r["step_errors"]
    assert len(r["competitors"]) == 2
    assert len(r["research"]) == 6
    assert r["calls"]["search"] == 33
    assert len(r["advice"]) == 1
    evidence = {p["id"] for s in r["scenarios"] for p in s["pages"]}
    evidence.update(p["id"] for s in r["research"] for p in s["pages"])
    assert all(c["evidence_id"] in evidence for c in r["advice"][0]["citations"])


@pytest.mark.asyncio
async def test_inflight_resume_reuses_search_but_finishes_models(client):
    providers = FakeProviders()
    a = await prepared(client, providers)
    ident = start(client, a)
    ex = Executor(ident, Run, providers)
    scenario = a["plan"]["scenarios"][0]
    await ex.search(f"search:{scenario['id']}", scenario["query"])
    with Session.begin() as db:
        db.get(Run,ident).status = "running"
    recover()
    await run_experiment(ident, providers)
    assert providers.searches.count(scenario["query"]) == 1
    assert client.get(f"/api/v1/runs/{ident}/report").json()["metrics"]["recommendation"]["denominator"] == 24
