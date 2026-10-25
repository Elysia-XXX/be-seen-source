import pytest
from app.schemas import Profile
from app.domain import validate_entities
from app.locations import location_relation, correct_location_suggestion
from .test_measurement import entity


def profile(**kwargs):
    return Profile(name="老王拌饭", city="广州市", district="海珠区", category="拌饭", **kwargs)


@pytest.mark.parametrize("address", ["", "广州市海珠区", "海珠区"])
def test_administrative_location_is_not_a_specific_branch(address):
    p = profile(address=address)
    assert p.scope == "city_brand"
    assert location_relation(p, "海珠区海联路12号") == "compatible"


@pytest.mark.parametrize("address", ["海珠区海联路12号", "广州市海珠区海联路12号", "廣州市海珠區海聯路12號"])
def test_more_detailed_same_area_is_compatible(address):
    assert location_relation(profile(), address) == "compatible"


def test_combined_city_district_input():
    p = Profile(name="老王拌饭", city="广州市海珠区", category="拌饭")
    assert location_relation(p, "海珠区海联路12号") == "compatible"


@pytest.mark.parametrize("status", ["other_store", "rejected", "suspected"])
def test_model_cannot_claim_branch_conflict_without_a_target_street_address(status):
    batch, pages = entity("广州市老王拌饭，海珠区海联路12号，拌饭。", city="广州市", address="海珠区海联路12号", status=status)
    batch.entities[0].reason = "来源门店地址与目标位置不符。"
    e = validate_entities(batch, profile(), pages)[0]
    assert e["status"] == "suspected"  # Compatible location doesn't establish identity.
    assert "兼容" in e["reason"]
    assert "不符" not in e["reason"]


def test_confirmed_entity_is_not_rejected_for_an_administrative_only_address():
    batch, pages = entity("广州市老王拌饭，海珠区海联路12号，拌饭。", city="广州市", address="海珠区海联路12号")
    assert validate_entities(batch, profile(address="广州市海珠区"), pages)[0]["status"] == "confirmed"


def test_road_without_district_is_not_proof_of_a_different_district():
    batch, pages = entity("广州市老王拌饭，新港中路452号，拌饭。", city="广州市", address="新港中路452号", status="other_store")
    batch.entities[0].reason = "地址与目标海珠区门店不同。"
    assert validate_entities(batch, profile(), pages)[0]["status"] == "suspected"


def test_different_name_is_not_rewritten_as_location_uncertainty():
    batch, pages = entity("广州市老李拌饭，海珠区海联路12号，拌饭。", name="老李拌饭", city="广州市", address="海珠区海联路12号", status="suspected")
    batch.entities[0].reason = "广州同城同品类，地址海珠区，名称与目标不同，无关联证据。"
    result = validate_entities(batch, profile(), pages)[0]
    assert result["reason"] == batch.entities[0].reason
    assert not result["is_target"]


@pytest.mark.parametrize("address,city,status", [
    ("越秀区东华路12号", "广州市", "other_store"),
    ("北京市海淀区中关村路12号", "北京市", "rejected"),
])
def test_explicit_conflicts_still_excluded(address, city, status):
    batch, pages = entity(f"{city}老王拌饭，{address}，拌饭。", city=city, address=address)
    assert validate_entities(batch, profile(), pages)[0]["status"] == status


def test_missing_components_do_not_prove_another_store():
    p = profile(address="广州市海珠区海联路12号")
    assert p.scope == "store"
    assert location_relation(p, "海珠区海联路") == "unknown"
    assert location_relation(p, "海珠区海联路13号") == "address_conflict"
    assert location_relation(p, "海珠区海联路112号") == "address_conflict"
    assert location_relation(p, "海珠区海联路12号") == "compatible"


def test_same_district_with_no_city_is_compatible_but_identity_stays_uncertain():
    batch, pages = entity("老王拌饭，海珠区海联路12号，拌饭。", city="", address="海珠区海联路12号")
    assert validate_entities(batch, profile(), pages)[0]["status"] == "suspected"


def test_enrichment_does_not_mislabel_address_expansion_as_conflict():
    item = {"field": "address", "value": "海珠区海联路12号", "status": "conflict", "reason": "位置不符", "citations": []}
    corrected = correct_location_suggestion(item, profile())
    assert corrected["status"] == "uncertain"
    assert "兼容" in corrected["reason"]
    actual_conflict = {**item, "value": "广州市越秀区东华路12号"}
    assert correct_location_suggestion(actual_conflict, profile()) == actual_conflict


def test_administrative_address_does_not_block_discovery_queries():
    from app.chat import quick_queries
    assert len(quick_queries(profile(address="广州市海珠区"))) == 6


def test_report_revision_is_idempotent_preserves_original_and_evidence():
    from copy import deepcopy
    from sqlalchemy import select, func
    from app.db import Session, Artifact, ChatTask, Conversation, Message
    from app.report_repairs import revise_locations
    from app.chat import report_graph
    batch, pages = entity("广州市老王拌饭，海珠区海联路12号，拌饭。", city="广州市", address="海珠区海联路12号", status="other_store")
    batch.entities[0].reason = "地址与目标海珠区门店不同。"
    original = {"title": "测试报告", "profile": profile().model_dump(), "blocks": [], "scenarios": [{
        "id": "q1", "query": "广州市拌饭", "question": "广州市拌饭", "match": "not_found",
        "search_ok": True, "pages": pages, "entities": [batch.entities[0].model_dump()], "recommendation": {}}]}
    snapshot = deepcopy(original)
    with Session.begin() as db:
        db.add(Conversation(id="c", profile=profile().model_dump()))
        db.add(ChatTask(id="t", conversation_id="c", idempotency_key="original", kind="discovery", status="completed", payload={}))
        db.add(Artifact(id="a", task_id="t", conversation_id="c", kind="discovery", version=1, report=original))
    revised_id = revise_locations("a")
    assert revised_id != "a"
    assert revise_locations("a") == revised_id
    with Session() as db:
        assert db.get(Artifact, "a").report == snapshot
        revised = db.get(Artifact, revised_id)
        assert revised.version == 2
        assert revised.report["scenarios"][0]["pages"] == pages
        assert revised.report["scenarios"][0]["entities"][0]["status"] == "suspected"
        assert revised.report["scenarios"][0]["match"] == "suspected"
        assert report_graph(revised.report)["nodes"]
        task = db.get(ChatTask, revised.task_id)
        assert task.search_calls == task.model_calls == 0
        assert db.scalar(select(func.count()).select_from(Message)) == 1
