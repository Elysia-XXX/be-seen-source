import re
import unicodedata
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from .schemas import Profile, Plan, GROUPS
from .locations import location_text, location_relation, profile_parts


def norm(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def canonical_url(value):
    p = urlparse(value)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return ""
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in ("spm", "from")]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", urlencode(sorted(query)), ""))


def text_of(page):
    return "\n".join(str(page.get(k) or "") for k in ("title", "passage", "content"))


def citation_texts(page):
    texts = [str(page.get(k) or '') for k in ('title', 'passage', 'content')]
    article = page.get('article', {})
    if article.get('status') == 'read':
        texts.append(article.get('text', ''))
    return texts


def aliases(profile):
    return [profile.name, *profile.aliases]


def validate_plan(plan: Plan, profile: Profile):
    forbidden = [*aliases(profile), profile.address if profile.scope == "store" else ""]
    if profile.website:
        forbidden.append(urlparse(profile.website).hostname or "")
    forbidden = [norm(x) for x in forbidden if x]
    seen_queries, seen_intents = set(), set()
    allowed_fields = set(Profile.model_fields)
    for s in plan.scenarios:
        for value in (s.query, s.question):
            if any(f in norm(value) for f in forbidden):
                raise ValueError(f"{s.id} 含品牌名、别名、域名或完整地址，请改成顾客需求")
        # Collapse common recommendation paraphrases before comparing queries.
        q = norm(s.query)
        for filler in ("请推荐", "推荐一下", "有哪些", "哪里有", "哪家好", "推荐", "哪里", "哪家", "有没有", "寻找"):
            q = q.replace(filler, "")
        intent = norm(s.intent)
        if not location_supported(profile.city, s.query):
            raise ValueError(f"{s.id} 搜索词需要明确包含测评城市")
        if q in seen_queries or intent in seen_intents:
            raise ValueError(f"{s.id} 与其他场景重复，请替换搜索意图")
        seen_queries.add(q)
        seen_intents.add(intent)
        for field in s.used_fields:
            if field not in allowed_fields or field in ("name", "aliases", "website", "address"):
                raise ValueError(f"{s.id} 使用了不允许的查询依据")
            if not getattr(profile, field):
                raise ValueError(f"{s.id} 使用了未填写的字段 {field}")
    return plan


def parse_pages(raw, prefix):
    import json
    pages = []
    raw_pages = raw.get("Pages")
    if not isinstance(raw_pages, list):
        raise ValueError("搜索结果 Pages 必须为数组")
    for rank, item in enumerate(raw_pages[:10], 1):
        try:
            p = json.loads(item) if isinstance(item, str) else item
            if not isinstance(p, dict):
                continue
        except (ValueError, TypeError):
            continue
        # Preserve provider rank, including duplicate URLs. Metrics deduplicate separately.
        pages.append({"id": f"{prefix}-e{rank:02}", "rank": rank,
                      **{k: str(p.get(k) or "") for k in ("title", "url", "date", "passage", "content", "site")},
                      "canonical_url": canonical_url(str(p.get("url") or ""))})
    if raw_pages and not pages:
        raise ValueError("搜索返回非空但无法解析，不能计为零结果")
    return pages


def valid_citations(citations, pages):
    by_id = {p["id"]: p for p in pages}
    good = []
    for c in citations:
        c = c.model_dump() if hasattr(c, "model_dump") else c
        page = by_id.get(c["evidence_id"])
        if page and page.get("canonical_url") and any(c["quote"] in text for text in citation_texts(page)):
            if len(norm(c["quote"])) >= 2:
                good.append(c)
    return good


def location_supported(location, text):
    token = norm(location_text(location))
    candidates = {token}
    if token.endswith("市") or token.endswith("区"):
        candidates.add(token[:-1])
    return any(len(x) >= 2 and x in norm(location_text(text)) for x in candidates)


def correct_entity_location(e, profile, texts):
    """Recheck negative location findings without promoting merchant identity."""
    e = dict(e)
    address = e["address"] if location_supported(e["address"], texts) else ""
    city = e["city"] if location_supported(e["city"], texts) else ""
    relation = location_relation(profile, address, city)
    location_rejection = e["status"] == "other_store" or (
        e["status"] in ("suspected", "rejected")
        and re.search(r"异地|(?:位置|地址|门店|分店|城市|区域|地区|区县)(?:(?!名称|店名|品牌名)[^。；;]){0,100}(?:不符|不同|冲突|不一致|不匹配|不在|非目标|其他门店)", e["reason"]))
    if location_rejection and not relation.endswith("_conflict"):
        e["status"] = "suspected"
        e["reason"] = ("来源位置与已填区域兼容；尚需确认商家身份，不能仅凭地址更详细判为其他门店。"
                       if relation == "compatible" else "没有足够位置证据认定为其他门店；商家身份及具体地址仍待确认。")
    return e


def validate_entities(batch, profile, pages):
    by_id = {p["id"]: p for p in pages}
    entities = []
    target_names = {norm(x) for x in aliases(profile)}
    for model_entity in batch.entities:
        e = model_entity.model_dump()
        citations = valid_citations(e["citations"], pages)
        if not citations:
            continue
        texts = "\n".join(text_of(by_id[c["evidence_id"]]) for c in citations)
        if norm(e["name"]) not in norm(texts):
            continue
        e["citations"] = citations
        e["is_target"] = norm(e["name"]) in target_names
        # A model's negative location conclusion needs evidence too. A district
        # is an area, not a specific branch; a more detailed address can fit it.
        address = e["address"] if location_supported(e["address"], texts) else ""
        city = e["city"] if location_supported(e["city"], texts) else ""
        relation = location_relation(profile, address, city)
        e = correct_entity_location(e, profile, texts)
        if e["status"] == "confirmed":
            if relation in ("city_conflict", "province_conflict"):
                e["status"] = "rejected"
                e["reason"] = "证据中的城市或省份与测评位置不同。"
            elif e["is_target"] and relation in ("district_conflict", "address_conflict"):
                e["status"] = "other_store"
                e["reason"] = "证据中的明确区县或具体门店地址与用户填写的位置不同。"
            elif not location_supported(profile_parts(profile)["city"], texts) or not city:
                e["status"] = "suspected"
                e["reason"] = "名称出现，但缺少可核验的同城证据。" 
            elif e["is_target"] and profile.scope == "store" and relation != "compatible":
                e["status"] = "suspected"
                e["reason"] = "当前证据不足以确认是指定地址的门店。"
            elif not e["is_target"] and (not e["category"] or norm(e["category"]) not in norm(texts)):
                e["status"] = "suspected"
                e["reason"] = "缺少可核验的相关品类描述。"
        entities.append(e)
    # Even if extraction omits the target, literal mentions must remain visible as uncertain.
    represented = {c["evidence_id"] for e in entities if e["is_target"] for c in e["citations"]}
    for p in pages:
        name = next((x for x in aliases(profile) if norm(x) in norm(text_of(p))), None)
        if name and p["id"] not in represented and p.get("canonical_url"):
            entities.append({"name": name, "status": "suspected", "is_target": True,
                             "city": "", "category": "", "address": "", "reason": "检索摘要出现名称，身份尚未确认。",
                             "citations": [{"evidence_id": p["id"], "quote": name}]})
    return entities


def validate_recommendations(batch, pages):
    by_id = {p["id"]: p for p in pages}
    accepted, rejected, seen = [], [], set()
    for rec in batch.recommendations:
        r = rec.model_dump()
        citations = valid_citations(r["citations"], pages)
        citations = [c for c in citations if norm(r["name"]) in norm(text_of(by_id[c["evidence_id"]]))]
        if not citations:
            rejected.append({"name": r["name"], "reason": "引用无效，或引用证据未出现商家名称"})
        elif norm(r["name"]) not in seen:
            r["citations"] = citations
            accepted.append(r)
            seen.add(norm(r["name"]))
    return {"recommendations": accepted, "rejected": rejected,
            "abstention_reason": batch.abstention_reason if not accepted else "",
            "validation_note": "引用和名称已核对；推荐理由仍需结合原始证据判断。"}


def apply_review(batch, candidate, pages):
    by_id = {p["id"]: p for p in pages}
    accepted, rejected = [], list(candidate.get("rejected", []))
    for rec in candidate.get("recommendations", []):
        check = next((c for c in batch.checks if norm(c.name) == norm(rec["name"])), None)
        citations = valid_citations(check.citations, pages) if check else []
        citations = [c for c in citations if norm(rec["name"]) in norm(text_of(by_id[c["evidence_id"]]))]
        if check and check.supported and citations:
            accepted.append({**rec, "citations": citations, "verification_reason": check.reason})
        else:
            rejected.append({"name": rec["name"], "reason": check.reason if check else "独立核验未提供支持结论。"})
    return {"recommendations": accepted, "rejected": rejected,
            "abstention_reason": "候选推荐缺少足够证据支持顾客需求或推荐理由。" if not accepted else "",
            "validation_note": "已完成独立的模型证据核验；仍需结合原始摘要判断。"}


def summarize_scene(scenario, search_step, entity_step, rec_step):
    pages = search_step.get("output", {}).get("pages", []) if search_step else []
    entities = entity_step.get("output", {}).get("entities", []) if entity_step else []
    confirmed = [e for e in entities if e["is_target"] and e["status"] == "confirmed"]
    uncertain = [e for e in entities if e["is_target"] and e["status"] == "suspected"]
    by_id = {p["id"]: p for p in pages}
    confirmed_ids = {c["evidence_id"] for e in confirmed for c in e["citations"]}
    ranks = [by_id[i]["rank"] for i in confirmed_ids if i in by_id]
    rec = rec_step.get("output", {}) if rec_step else {}
    names = {norm(e["name"]) for e in confirmed}
    selected = any(norm(r["name"]) in names and any(c["evidence_id"] in confirmed_ids for c in r["citations"])
                   for r in rec.get("recommendations", []))
    search_ok = bool(search_step and search_step["status"] == "completed")
    rec_ok = bool(rec_step and rec_step["status"] == "completed" and entity_step and entity_step["status"] == "completed")
    entities_ok = bool(entity_step and entity_step["status"] == "completed")
    return {**scenario, "search_ok": search_ok, "evaluation_ok": rec_ok,
            "match": "confirmed" if confirmed else "suspected" if uncertain else "unverified" if search_ok and not entities_ok else "not_found" if search_ok else "unavailable",
            "first_rank": min(ranks) if ranks else None, "selected": selected,
            "pages": pages, "entities": entities, "recommendation": rec,
            "errors": [x["error"] for x in (search_step, entity_step, rec_step) if x and x.get("error")],
            "confirmed_urls": list({by_id[i]["canonical_url"] for i in confirmed_ids if i in by_id and by_id[i]["canonical_url"]})}


def metrics(scenes):
    good = [s for s in scenes if s["search_ok"]]
    evaluated = [s for s in scenes if s["evaluation_ok"]]
    def rate(n, d):
        return {"numerator": n, "denominator": d, "percent": round(n / d * 100, 1) if d else None}
    urls = {u for s in scenes for u in s["confirmed_urls"]}
    return {"coverage": rate(sum(s["match"] == "confirmed" for s in good), len(good)),
            "top3": rate(sum(s["first_rank"] is not None and s["first_rank"] <= 3 for s in good), len(good)),
            "recommendation": rate(sum(s["selected"] for s in evaluated), len(evaluated)),
            "suspected": sum(s["match"] == "suspected" for s in good),
            "source_count": len(urls), "domain_count": len({urlparse(u).hostname for u in urls}),
            "identity_incomplete": sum(s["match"] == "unverified" for s in scenes),
            "search_failures": len(scenes) - len(good), "evaluation_incomplete": len(scenes) - len(evaluated)}


def rank_competitors(scenes):
    candidates = {}
    for s in scenes:
        recommended = {norm(r["name"]) for r in s["recommendation"].get("recommendations", [])}
        for e in s["entities"]:
            if e["is_target"] or e["status"] != "confirmed":
                continue
            key = norm(e["name"])
            c = candidates.setdefault(key, {"name": e["name"], "scenes": set(), "recommended": set(), "citations": []})
            c["scenes"].add(s["id"])
            if key in recommended:
                c["recommended"].add(s["id"])
            c["citations"].extend(e["citations"])
    ranked = sorted(candidates.values(), key=lambda c: (-len(c["scenes"]), -len(c["recommended"]), c["name"]))[:2]
    return [{**c, "scenes": sorted(c["scenes"]), "recommended": sorted(c["recommended"])} for c in ranked]
