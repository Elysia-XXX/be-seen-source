"""Query-first discovery: name mentions and public content, not identity gates."""
import re
from pydantic import BaseModel, Field
from .domain import norm, text_of, aliases, valid_citations
from .schemas import Citation


class ContentClue(BaseModel):
    evidence_id: str
    merchants: list[str] = Field(default_factory=list, max_length=10)
    angles: list[str] = Field(default_factory=list, max_length=5)
    citations: list[Citation] = Field(min_length=1, max_length=5)


class QueryContent(BaseModel):
    clues: list[ContentClue] = Field(default_factory=list, max_length=10)


def discovery_queries(profile):
    """Freeze five non-brand demand queries, followed by one profile lookup."""
    split = lambda values: list(dict.fromkeys(x.strip() for v in values for x in re.split(r"[，,、;；]", v) if len(x.strip()) > 1))
    categories = split([profile.category]) or [profile.category]
    candidates = [(f"{profile.city} {categories[0]}", "基础品类")]
    topics = [(f"{profile.city} {c}", "品类文章") for c in categories[1:]]
    topics += [(f"{profile.city} {tag} {categories[0]}", "特色标签文章") for tag in split(profile.tags)]
    if profile.district:
        topics += [(f"{profile.city} {profile.district} {categories[0]}", "区域体验文章")]
    topics += [(f"{profile.city} {categories[0]} {mode}", "服务体验文章") for mode in split(profile.service_modes)]
    candidates += [(q, group.replace('文章', '需求')) for q, group in topics]
    # Scenarios describe customer intentions, never unconfirmed merchant services.
    category = profile.category
    if any(w in category for w in ('饭', '餐', '火锅', '烧烤', '面', '粿条', '咖啡', '茶饮')):
        intents = ('朋友聚餐', '工作日午餐', '家庭用餐', '一个人吃饭')
    elif any(w in category for w in ('美容', '美发', '美甲', '理发', '美业')):
        intents = ('第一次体验', '日常护理', '重要场合造型', '下班后预约')
    elif any(w in category for w in ('健身', '瑜伽', '普拉提', '游泳')):
        intents = ('零基础入门', '下班后锻炼', '周末体验', '小班课程')
    else:
        intents = ('首次体验', '日常使用', '周末预约', '多人一起体验')
    candidates += [(f"{profile.city} {categories[0]} {intent}", "顾客使用场景") for intent in intents]
    from urllib.parse import urlparse
    forbidden = [*aliases(profile), profile.address if profile.scope == "store" else "", urlparse(profile.website).hostname or ""]
    result, seen = [], set()
    for query, group in candidates:
        if norm(query) in seen or any(norm(f) in norm(query) for f in forbidden if f):
            continue
        seen.add(norm(query))
        result.append({"query": query, "group": group, "search_kind": "discovery", "basis": "用户已填字段与顾客需求假设；不表示目标商家具备该服务"})
        if len(result) == 5:
            break
    if not result:
        raise ValueError("城市和品类包含目标名称，请填写通用城市、品类或标签后重试。")
    result.append({"query": f"{profile.city} {profile.name} 介绍 服务", "group": "品牌补充资料", "search_kind": "brand"})
    return result


def reading_hint(page):
    """A reading aid based on title/URL signals, not a verified genre label."""
    title, url = page.get("title", ""), page.get("url", "").lower()
    if re.search(r"排行榜|商户排行|榜单|排名|十大品牌|top\s*\d", title, re.I) or "/dishes/list" in url:
        return {"kind": "ranking", "label": "榜单线索", "reason": "标题或链接含排行、榜单特征"}
    if any(x in url for x in ("/restaurant/", "you.ctrip.com/food/", "map.so.com", "ditu.so.com", "/cater/", "/mendian/", "openrice.com")):
        return {"kind": "listing", "label": "门店资料线索", "reason": "链接路径呈现门店、地图或目录特征"}
    if (re.search(r"探店|体验|食记|食記|推荐理由|宝藏店|寶藏店|一口|尝鲜|攻略|测评|打卡|分享", title)
            or any(x in url for x in ("/article/", "/thread-", "/topic/", "weibo.com/", "/newspage/", "/shipu/", "mp.weixin.qq.com/s/"))):
        return {"kind": "article", "label": "文章 / 分享线索", "reason": "标题或链接含文章、体验分享特征"}
    return {"kind": "other", "label": "待判断内容", "reason": "仅凭标题和链接不能确定内容类型"}


def reading_order(pages):
    priorities = {"article": 0, "other": 1, "ranking": 2, "listing": 3}
    return sorted(pages, key=lambda p: (priorities[reading_hint(p)["kind"]], p.get("rank", 0)))


def reading_view(report):
    # Derived display metadata; preserve stored evidence and original provider ranks.
    from copy import deepcopy
    result = deepcopy(report)
    for scene in result.get("scenarios", []):
        for page in scene.get("pages", []):
            page["reading_hint"] = reading_hint(page)
        scene["reading_ids"] = [p["id"] for p in reading_order(scene.get("pages", []))]
    for page in result.get("context_evidence", []) + [p for s in result.get("research", []) for p in s.get("pages", [])]:
        page["reading_hint"] = reading_hint(page)
    return result


def article_followups(plan, searches):
    if any(q.get("search_kind") == "content" for q in plan):
        return []
    additions = []
    for i, item in enumerate(plan):
        if item.get("search_kind") != "discovery":
            continue
        pages = searches.get(f"lookup:{i}", {}).get("output", {}).get("pages", [])
        kinds = [reading_hint(p)["kind"] for p in pages]
        # Fill a concrete content gap, including successful searches with no results.
        step = searches.get(f"lookup:{i}", {})
        if step.get('status') != 'completed' or "article" in kinds:
            continue
        suffix = "体验分享 图文" if "探店" in item["query"] else "探店体验 推荐理由 图文"
        query = item["query"] + " " + suffix
        if query not in {q["query"] for q in plan + additions}:
            additions.append({"query": query, "group": "文章追查", "search_kind": "content", "parent_query": item["query"], "reason": "原查询未出现明确文章线索，补充具体体验与推荐理由"})
        if len(additions) == 2:
            break
    return additions


def name_mentions(profile, pages):
    """Count visible name strings even when identity is not established."""
    found = []
    for page in pages:
        for name in aliases(profile):
            field = next((str(page.get(k) or "") for k in ("title", "passage", "content")
                          if norm(name) in norm(str(page.get(k) or ""))), "")
            if field:
                found.append({"name": name, "evidence_id": page["id"], "rank": page["rank"],
                              "quote": field, "identity_note": "名称在本次摘要中出现；是否同一商家另行核对，不影响名称提及记录。"})
                break
    return found


def checked_content(batch, pages):
    by_id, result = {p["id"]: p for p in pages}, []
    for item in batch.clues:
        page = by_id.get(item.evidence_id)
        if not page:
            continue
        citations = [c for c in valid_citations(item.citations, pages) if c["evidence_id"] == item.evidence_id]
        if not citations:
            continue
        quotes = [c["quote"] for c in citations]
        # Excerpts, including purported selling points, must be literal evidence.
        merchants = list(dict.fromkeys(n for n in item.merchants if len(norm(n)) >= 2 and any(n in q for q in quotes)))
        angles = list(dict.fromkeys(a for a in item.angles if len(norm(a)) >= 2 and any(a in q for q in quotes)))
        if merchants or angles:
            result.append({"evidence_id": item.evidence_id, "merchants": merchants, "angles": angles, "citations": citations})
    return {"clues": result}


def decorate_scene(scene, profile, content=None):
    mentions = name_mentions(profile, scene["pages"])
    scene["mentions"] = mentions
    scene["mention_status"] = "mentioned" if mentions else "not_mentioned" if scene["search_ok"] else "unavailable"
    scene["mention_first_rank"] = min((m["rank"] for m in mentions), default=None)
    scene["content_clues"] = (content or {}).get("clues", [])
    return scene


def exposure_summary(scenes):
    demands = [s for s in scenes if s.get("search_kind") == "discovery"]
    successful = [s for s in demands if s["search_ok"]]
    return {"planned_queries": len(demands), "successful_queries": len(successful),
            "mentioned_queries": sum(bool(s.get("mentions")) for s in successful),
            "failed_queries": len(demands) - len(successful),
            "basis": "用户提供的品类、标签和区域组合；只统计返回标题与摘要中的名称出现，不代表知名度或已确认商家身份。"}


def query_context(scenes):
    return [{"id": s["id"], "query": s["query"], "group": s.get("group"), "search_kind": s.get("search_kind"),
             "search_ok": s["search_ok"], "mention_status": s.get("mention_status"),
             "mention_first_rank": s.get("mention_first_rank"), "content_clues": s.get("content_clues", []),
             "source_ids": [p["id"] for p in s.get("pages", [])]} for s in scenes[-24:]]


def content_competitors(scenes, profile):
    candidates = {}
    for scene in scenes:
        if scene.get("search_kind") == "brand":
            continue
        for clue in scene.get("content_clues", []):
            for name in clue["merchants"]:
                if any(norm(n) in norm(name) for n in aliases(profile)):
                    continue
                entry = candidates.setdefault(norm(name), {"name": name, "scenes": set(), "citations": [], "basis": "同一查询结果中出现的商家名称，供内容参考；同城、品类与门店身份未作确认。"})
                entry["scenes"].add(scene["id"])
                entry["citations"].extend(clue["citations"])
    ranked = sorted(candidates.values(), key=lambda c: (-len(c["scenes"]), c["name"]))[:8]
    return [{**c, "scenes": sorted(c["scenes"]), "citations": list({(x["evidence_id"], x["quote"]): x for x in c["citations"]}.values())} for c in ranked]
