import asyncio
from sqlalchemy import select
from pydantic import ValidationError
from .config import settings
from .db import Session, Analysis, Run, Step, serialize, now
from .schemas import Profile, Plan, Enrichment, EntityBatch, Recommendations, RecommendationReview, AdviceBatch, GROUPS
from .domain import (parse_pages, validate_plan, valid_citations, validate_entities,
                     validate_recommendations, apply_review, summarize_scene, metrics, rank_competitors, norm)
from .providers import Providers, ProviderError
from .prompts import messages, public_evidence, recommend_input, VERSION


class Stopped(Exception):
    pass


async def settled(*jobs):
    """Wait for in-flight work before finalizing a cancelled or failed owner."""
    results = await asyncio.gather(*jobs, return_exceptions=True)
    error = next((r for r in results if isinstance(r, BaseException)), None)
    if error:
        raise error
    return results


def steps_for(owner):
    with Session() as db:
        return {s.key: serialize(s) for s in db.scalars(select(Step).where(Step.owner_id == owner))}


def update_row(cls, ident, **values):
    with Session.begin() as db:
        row = db.get(cls, ident)
        for k, v in values.items():
            setattr(row, k, v)


class Executor:
    def __init__(self, owner, cls, providers=None, search_limit=None, model_limit=None):
        self.owner, self.cls = owner, cls
        self.providers = providers or Providers()
        self.cfg = settings()
        self.search_sem = asyncio.Semaphore(self.cfg.search_concurrency)
        self.model_sem = asyncio.Semaphore(self.cfg.model_concurrency)
        self.fatal = ""
        self.search_limit = self.cfg.search_limit if search_limit is None else search_limit
        self.model_limit = self.cfg.model_limit if model_limit is None else model_limit

    def check(self):
        with Session() as db:
            row = db.get(self.cls, self.owner)
            if self.fatal or getattr(row, "cancel_requested", False):
                raise Stopped(self.fatal or "用户取消了后续步骤。")

    def reserve(self, kind):
        self.check()
        field = "search_calls" if kind == "search" else "model_calls"
        limit = self.search_limit if kind == "search" else self.model_limit
        with Session.begin() as db:
            row = db.get(self.cls, self.owner)
            if getattr(row, field) >= limit:
                raise ProviderError("已达到本次搜索调用上限。" if kind == "search" else "已达到本次模型调用上限。")
            setattr(row, field, getattr(row, field) + 1)

    def save(self, key, **values):
        with Session.begin() as db:
            step = db.scalar(select(Step).where(Step.owner_id == self.owner, Step.key == key))
            if not step:
                step = Step(owner_id=self.owner, key=key)
                db.add(step)
            for k, v in values.items():
                setattr(step, k, v)
            step.updated_at = now()

    async def execute(self, key, kind, input_data, action, validate):
        self.check()
        previous = steps_for(self.owner).get(key)
        if previous and previous["status"] in ("completed", "failed"):
            return previous["output"] if previous["status"] == "completed" else None
        attempts = previous["attempts"] if previous else 0
        self.save(key, status="running", input=input_data)
        sem = self.search_sem if kind == "search" else self.model_sem
        async with sem:
            error = "上次执行中断，重试次数已耗尽。"
            while attempts < 3:
                try:
                    self.reserve(kind)
                    attempts += 1
                    self.save(key, attempts=attempts)
                    output = validate(await action())
                    self.save(key, status="completed", output=output, error="")
                    return output
                except Stopped:
                    raise
                except ProviderError as e:
                    error = str(e)
                    if e.fatal:
                        self.fatal = error
                    if not e.retryable or attempts >= 3:
                        break
                except (ValueError, ValidationError, KeyError, TypeError) as validation_error:
                    error = "返回内容未通过结构或证据校验；请检查模型能力或调整场景。"
                    if kind == "model":
                        input_data["messages"].append({"role": "user", "content":
                            "上一轮 JSON 未通过校验。请遵守原规则，修正以下问题并重新输出完整 JSON：" + str(validation_error)[:1500]})
                        self.save(key, input=input_data)
                    if attempts >= 2:
                        break
                if attempts < 3:
                    await asyncio.sleep(min(2 ** attempts, 4))
            self.save(key, status="failed", error=error)
            return None

    async def search(self, key, query):
        def validate(raw):
            return {"pages": parse_pages(raw, getattr(self, "evidence_prefix", "") + key.replace(":", "-")), "raw": raw,
                    "queried_at": now(), "request_id": raw.get("RequestId", ""), "query": query}
        return await self.execute(key, "search", {"query": query, "Mode": 0, "Cnt": 10},
                                  lambda: self.providers.search(query), validate)

    async def model(self, key, kind, data, schema, post=lambda v: v.model_dump()):
        prompt = messages(kind, data, schema)
        def validate(raw):
            result = post(schema.model_validate(raw["data"]))
            return {**result, "_model": raw.get("model", self.cfg.model), "_usage": raw.get("usage", {})}
        return await self.execute(key, "model", {"messages": prompt, "prompt_version": VERSION,
                                  "model": self.cfg.model, "temperature": 0}, lambda: self.providers.llm(prompt), validate)

    async def article(self, url):
        from .articles import article_key
        self.check()
        key = article_key(url)
        steps = steps_for(self.owner)
        if steps.get(key, {}).get('status') == 'completed':
            return steps[key]['output']
        if key not in steps and sum(k.startswith('article:') for k in steps) >= 3:
            return None
        self.save(key, status='running', input={'url': url}, attempts=1)
        reader = getattr(self.providers, 'read_article', None)
        output = await reader(url) if reader else {'status': 'unavailable', 'url': url, 'text': '', 'reason': '当前执行器未提供正文读取。'}
        self.save(key, status='completed', output=output, error='')
        self.check()
        return output


async def prepare(ident, providers=None):
    update_row(Analysis, ident, status="preparing", error="")
    ex = Executor(ident, Analysis, providers)
    try:
        with Session() as db:
            row = db.get(Analysis, ident)
            profile = Profile.model_validate(row.profile)
            version = row.plan.get("version", 0) + 1
            enrichment = row.enrichment
        queries = [f"{profile.city} {profile.name}", f"{profile.city} {profile.name} {profile.category}",
                   f"{profile.city} {profile.name} 地址 服务"]
        searches = await settled(*(ex.search(f"profile:{i}", q) for i, q in enumerate(queries)))
        if ex.fatal:
            raise Stopped(ex.fatal)
        pages = [p for result in searches if result for p in result["pages"]]
        def validate_enrichment(batch):
            accepted = []
            for s in batch.suggestions:
                citations = valid_citations(s.citations, pages)
                if citations and any(norm(s.value) in norm(c["quote"]) for c in citations):
                    from .locations import correct_location_suggestion
                    accepted.append(correct_location_suggestion({**s.model_dump(), "citations": citations, "accepted": False}, profile))
            return {"suggestions": accepted}
        if "suggestions" not in enrichment:
            result = await ex.model("enrichment", "enrichment", {"profile": profile.model_dump(), "evidence": public_evidence(pages)}, Enrichment, validate_enrichment)
            enrichment = {"suggestions": result.get("suggestions", []) if result else [], "pages": pages,
                          "original_profile": profile.model_dump(), "field_origins": {k: "user" for k, v in profile.model_dump().items() if v}}
            update_row(Analysis, ident, enrichment=enrichment)
        plan = await ex.model(f"plan:{version}", "plan", {"confirmed_profile": profile.model_dump(), "version": version}, Plan,
                              lambda p: validate_plan(p, profile).model_dump())
        if not plan:
            raise Stopped(ex.fatal or "场景生成失败。请检查模型配置后重试。")
        plan = Plan.model_validate(plan).model_dump()
        plan["version"] = version
        update_row(Analysis, ident, plan=plan, status="awaiting_confirmation")
    except Stopped as e:
        update_row(Analysis, ident, status="failed", error=str(e))
    except Exception:
        update_row(Analysis, ident, status="failed", error="建档任务异常，请检查服务端日志和配置后重试。")
        raise


def scene_results(run, steps):
    return [{**summarize_scene(s, steps.get(f"search:{s['id']}"), steps.get(f"entities:{s['id']}"), steps.get(f"verify:{s['id']}")),
             "queried_at": steps.get(f"search:{s['id']}", {}).get("output", {}).get("queried_at"),
             "request_id": steps.get(f"search:{s['id']}", {}).get("output", {}).get("request_id")}
            for s in run.snapshot["plan"]["scenarios"]]


def report_for(run):
    steps = steps_for(run.id)
    scenes = scene_results(run, steps)
    competitors = steps.get("competitors", {}).get("output", {}).get("competitors", [])
    advice = steps.get("advice", {}).get("output", {})
    research = [{"key": k, **s["output"], "error": s["error"]} for k, s in steps.items() if k.startswith("competitor-search:")]
    return {"id": run.id, "analysis_id": run.analysis_id, "status": run.status, "error": run.error,
            "created_at": run.created_at, "finished_at": run.finished_at, "snapshot": run.snapshot,
            "calls": {"search": run.search_calls, "model": run.model_calls, "search_limit": settings().search_limit, "model_limit": settings().model_limit},
            "metrics": metrics(scenes), "groups": {g: metrics([s for s in scenes if s["group"] == g]) for g in GROUPS},
            "scenarios": scenes, "competitors": competitors, "research": research,
            "advice": advice.get("advice", []), "limitations": advice.get("limitations", []),
            "step_errors": [{"key": k, "error": s["error"]} for k, s in steps.items() if s["status"] == "failed"],
            "methodology": ["仅统计本次搜索返回标题及摘要中的已确认提及，未发现不代表全网不存在。",
                "场景由商家资料和模型生成，不代表真实搜索量；各场景等权。",
                "模拟推荐仅代表本系统在本次证据和模型配置下的一次实验。",
                "搜索结果位置是包含商家的网页位置，不是商家排名。",
                "链接由搜索服务返回；有链接不代表系统已验证其可访问。"],
            "progress": {"search_done": sum(s["search_ok"] for s in scenes),
                         "evaluated": sum(s["evaluation_ok"] for s in scenes), "total": len(scenes),
                         "steps_done": sum(s["status"] in ("completed", "failed") for s in steps.values())}}


async def run_experiment(ident, providers=None):
    update_row(Run, ident, status="running", error="")
    ex = Executor(ident, Run, providers)
    try:
        with Session() as db:
            run = db.get(Run, ident)
            profile = Profile.model_validate(run.snapshot["profile"])
            plan = Plan.model_validate(run.snapshot["plan"])
            coverage_only = run.snapshot.get("mode") == "coverage"
            ex.model_limit = min(ex.model_limit, run.snapshot.get("model_budget", ex.model_limit))
        validate_plan(plan, profile)

        async def process(s):
            result = await ex.search(f"search:{s.id}", s.query)
            if result is None:
                return
            pages = result["pages"]
            if coverage_only:
                if pages:
                    await ex.model(f"entities:{s.id}", "entities", {"profile": profile.model_dump(), "scope": profile.scope,
                        "evidence": public_evidence(pages)}, EntityBatch,
                        lambda batch: {"entities": validate_entities(batch, profile, pages)})
                else:
                    ex.save(f"entities:{s.id}", status="completed", output={"entities": []})
                return
            # Empty search is a valid experiment, not an API failure.
            if not pages:
                ex.save(f"entities:{s.id}", status="completed", output={"entities": []})
                ex.save(f"recommend:{s.id}", status="completed", output={"recommendations": [], "rejected": [], "abstention_reason": "本次搜索未返回可用证据。"})
                ex.save(f"verify:{s.id}", status="completed", output={"recommendations": [], "rejected": [], "abstention_reason": "本次搜索未返回可用证据。"})
                return
            _, candidate = await settled(
                ex.model(f"entities:{s.id}", "entities", {"profile": profile.model_dump(), "scope": profile.scope,
                    "evidence": public_evidence(pages)}, EntityBatch,
                    lambda batch: {"entities": validate_entities(batch, profile, pages)}),
                ex.model(f"recommend:{s.id}", "recommend", recommend_input(s.question, pages), Recommendations,
                    lambda batch: validate_recommendations(batch, pages)))
            if candidate is not None:
                if candidate.get("recommendations"):
                    await ex.model(f"verify:{s.id}", "verify", {**recommend_input(s.question, pages),
                        "candidates": candidate["recommendations"]}, RecommendationReview,
                        lambda batch: apply_review(batch, candidate, pages))
                else:
                    ex.save(f"verify:{s.id}", status="completed", output=candidate)

        # Bounded scene scheduling keeps cancellation from launching all remaining searches.
        queue = asyncio.Queue()
        for s in plan.scenarios:
            queue.put_nowait(s)
        async def lane():
            while not queue.empty():
                ex.check()
                scenario = queue.get_nowait()
                await process(scenario)
        outcomes = await asyncio.gather(*(lane() for _ in range(3)), return_exceptions=True)
        stopped = next((o for o in outcomes if isinstance(o, Exception)), None)
        if stopped:
            raise stopped
        ex.check()
        with Session() as db:
            run = db.get(Run, ident)
        scenes = scene_results(run, steps_for(ident))
        competitors = rank_competitors(scenes)
        ex.save("competitors", status="completed", output={"competitors": competitors})
        missing = next((s["query"] for s in scenes if s["search_ok"] and s["match"] != "confirmed"), profile.category)
        requests = []
        for i, c in enumerate(competitors):
            for j, suffix in enumerate(["官方 服务介绍", "探店 测评 达人 视频", missing]):
                requests.append(ex.search(f"competitor-search:{i}:{j}", f"{profile.city} {c['name']} {suffix}"))
        research = await settled(*requests) if requests else []
        ex.check()
        if competitors:
            # The summary sees a bounded evidence sample, not 24 full SERPs concatenated.
            competitor_ids = {c["evidence_id"] for comp in competitors for c in comp["citations"]}
            summary_evidence = [p for s in scenes for p in s["pages"] if p["id"] in competitor_ids][:8]
            summary_evidence += [p for r in research if r for p in r["pages"][:3]]
            def check_advice(batch):
                valid = []
                for a in batch.advice:
                    citations = valid_citations(a.citations, summary_evidence)
                    if citations:
                        valid.append({**a.model_dump(), "citations": citations})
                return {"advice": valid, "limitations": batch.limitations}
            await ex.model("advice", "advice", {"profile": profile.model_dump(), "metrics": metrics(scenes),
                "scenes": [{k: s[k] for k in ("id", "query", "match", "selected")} for s in scenes],
                "competitors": competitors, "evidence": public_evidence(summary_evidence)}, AdviceBatch, check_advice)
        else:
            ex.save("advice", status="completed", output={"advice": [], "limitations": ["未找到身份及品类依据充分的同城竞品，暂不生成营销建议。"]})
        failed = any(s["status"] == "failed" for s in steps_for(ident).values())
        update_row(Run, ident, status="partial" if failed else "completed", finished_at=now())
    except Stopped as e:
        with Session() as db:
            cancelled = db.get(Run, ident).cancel_requested
        has_data = any(s["status"] == "completed" for s in steps_for(ident).values())
        update_row(Run, ident, status="cancelled" if cancelled else "partial" if has_data else "failed", error=str(e), finished_at=now())
    except Exception:
        update_row(Run, ident, status="partial" if steps_for(ident) else "failed", error="任务发生异常；已保留完成的证据，请检查服务端日志。", finished_at=now())
        raise
