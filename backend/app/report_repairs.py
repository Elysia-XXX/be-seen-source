"""Explicit, idempotent revisions of saved discovery reports; no API calls."""
from copy import deepcopy
from sqlalchemy import select, func, text
from .db import Session, Artifact, ChatTask, Message, uid, now
from .schemas import Profile
from .domain import correct_entity_location, text_of, rank_competitors, valid_citations
from .locations import correct_location_suggestion


def revise_locations(artifact_id):
    with Session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        source = db.get(Artifact, artifact_id)
        if not source or source.kind != "discovery":
            raise ValueError("只能修订已保存的快速发现报告。")
        key = "location-revision-2:" + source.id
        previous = db.scalar(select(ChatTask).where(ChatTask.conversation_id == source.conversation_id, ChatTask.idempotency_key == key))
        if previous:
            return db.scalar(select(Artifact.id).where(Artifact.task_id == previous.id))
        if db.scalar(select(ChatTask.id).where(ChatTask.conversation_id == source.conversation_id, ChatTask.status.in_(["queued", "running"]))):
            raise ValueError("对话仍有运行任务，请待任务结束后修订。")
        report = deepcopy(source.report)
        profile = Profile.model_validate(report["profile"])
        changes = []
        for scene in report.get("scenarios", []):
            pages = scene.get("pages", [])
            by_id = {p["id"]: p for p in pages}
            for index, entity in enumerate(scene.get("entities", [])):
                citations = valid_citations(entity["citations"], pages)
                texts = "\n".join(text_of(by_id[c["evidence_id"]]) for c in citations)
                fixed = correct_entity_location(entity, profile, texts)
                if fixed != entity:
                    changes.append({"scene_id": scene["id"], "before": entity, "after": fixed})
                    scene["entities"][index] = fixed
            # Negative findings only become uncertain; confirmed metrics stay unchanged.
            if scene.get("match") == "not_found" and any(e["is_target"] and e["status"] == "suspected" for e in scene.get("entities", [])):
                scene["match"] = "suspected"
        for index, item in enumerate(report.get("suggestions", [])):
            fixed = correct_location_suggestion(item, profile)
            if fixed != item:
                changes.append({"suggestion_index": index, "before": item, "after": fixed})
                report["suggestions"][index] = fixed
        if not changes:
            return source.id
        notice = "已修正位置判断：只填写城市和区县不等于指定具体门店，来源地址更详细也不构成冲突。缺乏冲突证据的条目改为待确认，商家身份仍需证据支持。本次使用原检索结果，未追加搜索。"
        report["location_revision"] = {"source_artifact_id": source.id, "version": 2, "changes": changes}
        report["title"] += " · 位置判断修订"
        report["blocks"] = [{"kind": "uncertainty", "text": notice, "citations": []}, *report.get("blocks", [])]
        report["competitors"] = rank_competitors(report.get("scenarios", []))
        task_id, new_id = uid(), uid()
        version = (db.scalar(select(func.max(Artifact.version)).where(Artifact.conversation_id == source.conversation_id)) or 0) + 1
        db.add(ChatTask(id=task_id, conversation_id=source.conversation_id, idempotency_key=key, kind="discovery", status="completed",
                        payload={"action": "location_revision", "source_artifact_id": source.id}, finished_at=now()))
        db.add(Artifact(id=new_id, task_id=task_id, conversation_id=source.conversation_id, kind=source.kind, version=version, report=report))
        db.add(Message(conversation_id=source.conversation_id, role="assistant", task_id=task_id, content={"text": notice, "artifact_id": new_id}))
        db.commit()
        return new_id
