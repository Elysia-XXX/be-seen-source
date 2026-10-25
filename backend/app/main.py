import hashlib
import hmac
import secrets
import time
import json
from contextlib import asynccontextmanager
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from .config import settings
from .db import init_db, Session, Analysis, Run, Step, serialize, uid
from .schemas import Profile, PlanEdit, RunCreate, Plan
from .domain import validate_plan
from .engine import report_for, steps_for
from .prompts import VERSION


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="被看见 · 搜索可见度实验", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings().cors_origins.split(","),
                   allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
login_failures = defaultdict(list)


def signature(payload):
    return hmac.new(settings().demo_password.encode(), payload.encode(), hashlib.sha256).hexdigest()


def require_auth(authorization: str = Header(default="")):
    try:
        scheme, token = authorization.split(" ", 1)
        expires, nonce, sig = token.split(".")
        if scheme != "Bearer" or int(expires) < time.time() or not hmac.compare_digest(signature(f"{expires}.{nonce}"), sig):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "请输入工作空间访问口令。") from None


class Login(BaseModel):
    password: str = Field(max_length=500)


@app.get("/api/v1/health")
def health():
    cfg = settings()
    return {"ok": True, "configured": cfg.configured,
            "services": {"search": bool(cfg.tencentcloud_secret_id and cfg.tencentcloud_secret_key),
                         "model": bool(cfg.base_url and cfg.api_key and cfg.model)},
            "model": cfg.model or None}


@app.post("/api/v1/auth/session")
def login(body: Login, request: Request):
    ip = request.client.host if request.client else "local"
    recent = [t for t in login_failures[ip] if time.time() - t < 60]
    login_failures[ip] = recent
    if len(recent) >= 5:
        raise HTTPException(429, "口令尝试过于频繁，请一分钟后再试。")
    if not hmac.compare_digest(body.password.encode(), settings().demo_password.encode()):
        login_failures[ip].append(time.time())
        raise HTTPException(401, "访问口令不正确。")
    payload = f"{int(time.time()) + 43200}.{secrets.token_hex(16)}"
    return {"token": f"{payload}.{signature(payload)}", "expires_in": 43200}


AUTH = [Depends(require_auth)]

from .chat import make_router
app.include_router(make_router(require_auth))


@app.get("/api/v1/analyses", dependencies=AUTH)
def list_analyses():
    with Session() as db:
        rows = db.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(30)).all()
        result = []
        for a in rows:
            latest = db.scalar(select(Run).where(Run.analysis_id == a.id).order_by(Run.created_at.desc()))
            result.append({"id": a.id, "name": a.profile["name"], "city": a.profile["city"], "status": a.status,
                           "created_at": a.created_at, "latest_run": latest.id if latest else None})
        return result


@app.post("/api/v1/analyses", dependencies=AUTH, status_code=202)
def create_analysis(body: Profile):
    if not settings().configured:
        raise HTTPException(503, "搜索或模型服务尚未配置，请先完成服务端 .env 设置。")
    with Session.begin() as db:
        row = Analysis(profile=body.model_dump())
        db.add(row)
        db.flush()
        return serialize(row)


@app.get("/api/v1/analyses/{ident}", dependencies=AUTH)
def get_analysis(ident: str):
    with Session() as db:
        row = db.get(Analysis, ident)
        if not row:
            raise HTTPException(404, "未找到这次分析。")
        result = serialize(row)
        result["progress"] = [{"key": k, "status": v["status"], "error": v["error"]} for k, v in steps_for(ident).items()]
        return result


@app.get("/api/v1/analyses/{ident}/runs", dependencies=AUTH)
def run_history(ident: str):
    with Session() as db:
        if not db.get(Analysis, ident):
            raise HTTPException(404, "分析不存在。")
        return [{"id": r.id, "status": r.status, "created_at": r.created_at}
                for r in db.scalars(select(Run).where(Run.analysis_id == ident).order_by(Run.created_at.desc()))]


@app.patch("/api/v1/analyses/{ident}/plan", dependencies=AUTH)
def edit_plan(ident: str, body: PlanEdit):
    try:
        validate_plan(body.plan, body.profile)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    with Session.begin() as db:
        row = db.get(Analysis, ident)
        if not row:
            raise HTTPException(404, "分析不存在。")
        if row.status != "awaiting_confirmation":
            raise HTTPException(409, "此计划已冻结或正在生成，不能修改。")
        if row.plan["version"] != body.expected_version:
            raise HTTPException(409, "计划已更新，请刷新后再保存。")
        if any(body.profile.model_dump()[k] != row.profile[k] for k in ("name", "city", "category")):
            raise HTTPException(422, "修改名称、城市或主营品类需要新建分析，避免复用另一商家的建档证据。")
        enrichment = dict(row.enrichment)
        suggestions = [dict(s) for s in enrichment.get("suggestions", [])]
        if any(i < 0 or i >= len(suggestions) for i in body.accepted_suggestions):
            raise HTTPException(422, "资料建议编号无效。")
        origins = dict(enrichment.get("field_origins", {}))
        for k, v in body.profile.model_dump().items():
            if v != row.profile.get(k):
                origins[k] = "user_confirmed"
        for i, suggestion in enumerate(suggestions):
            suggestion["accepted"] = i in body.accepted_suggestions
            if suggestion["accepted"]:
                value = body.profile.model_dump()[suggestion["field"]]
                if suggestion["value"] not in (value if isinstance(value, list) else [value]):
                    raise HTTPException(422, "已采纳建议必须体现在商家资料中。")
                origins[suggestion["field"]] = "search_confirmed"
        row.enrichment = {**enrichment, "suggestions": suggestions, "field_origins": origins}
        row.profile = body.profile.model_dump()
        row.plan = {**body.plan.model_dump(), "version": body.expected_version + 1}
        return serialize(row)


@app.post("/api/v1/analyses/{ident}/regenerate", dependencies=AUTH, status_code=202)
def regenerate(ident: str):
    with Session.begin() as db:
        row = db.get(Analysis, ident)
        if not row or row.status not in ("awaiting_confirmation", "failed"):
            raise HTTPException(409, "仅待确认或生成失败的分析可以重新生成。")
        if row.search_calls >= settings().search_limit or row.model_calls >= settings().model_limit:
            raise HTTPException(409, "调用预算已耗尽，请新建分析。")
        # Keep completed search snapshots. Only failed steps are eligible for an explicit retry.
        db.execute(delete(Step).where(Step.owner_id == ident, Step.status == "failed"))
        row.status, row.error = "queued", ""
        return {"id": ident, "status": "queued"}


@app.post("/api/v1/analyses/{ident}/runs", dependencies=AUTH, status_code=202)
def create_run(ident: str, body: RunCreate, idempotency_key: str = Header(min_length=8, max_length=128)):
    # Serialize the check/create transaction so simultaneous double-clicks cannot race.
    with Session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prior = db.scalar(select(Run).where(Run.analysis_id == ident, Run.idempotency_key == idempotency_key))
        if prior:
            if prior.snapshot["plan"]["version"] != body.plan_version:
                raise HTTPException(409, "幂等键已用于另一个计划版本。")
            return {"id": prior.id, "status": prior.status}
        analysis = db.get(Analysis, ident)
        if not analysis:
            raise HTTPException(404, "分析不存在。")
        if analysis.status not in ("awaiting_confirmation", "frozen") or analysis.plan["version"] != body.plan_version:
            raise HTTPException(409, "计划未就绪或版本已变化，请刷新后再启动。")
        running = db.scalar(select(Run).where(Run.analysis_id == ident, Run.status.in_(["queued", "running"])))
        if running:
            raise HTTPException(409, "该分析已有正在执行的测评，请等待完成。")
        try:
            validate_plan(Plan.model_validate(analysis.plan), Profile.model_validate(analysis.profile))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        cfg = settings()
        run = Run(id=uid(), analysis_id=ident, idempotency_key=idempotency_key,
                  snapshot={"profile": analysis.profile, "plan": analysis.plan, "enrichment": analysis.enrichment,
                            "search": {"provider": "元宝搜索", "Mode": 0, "Cnt": 10},
                            "model": cfg.model, "temperature": 0, "prompt_version": VERSION},
                  search_calls=analysis.search_calls, model_calls=analysis.model_calls)
        analysis.status = "frozen"
        db.add(run)
        db.commit()
        return {"id": run.id, "status": run.status}


def get_run(ident):
    with Session() as db:
        row = db.get(Run, ident)
        if not row:
            raise HTTPException(404, "测评不存在。")
        return row


@app.get("/api/v1/runs/{ident}", dependencies=AUTH)
def run_status(ident: str):
    report = report_for(get_run(ident))
    return {k: report[k] for k in ("id", "analysis_id", "status", "error", "progress", "calls", "step_errors")}


@app.post("/api/v1/runs/{ident}/cancel", dependencies=AUTH)
def cancel(ident: str):
    with Session.begin() as db:
        run = db.get(Run, ident)
        if not run:
            raise HTTPException(404, "测评不存在。")
        if run.status in ("queued", "running"):
            run.cancel_requested = 1
        return {"id": ident, "cancel_requested": bool(run.cancel_requested)}


@app.get("/api/v1/runs/{ident}/report", dependencies=AUTH)
def report(ident: str):
    return report_for(get_run(ident))


@app.get("/api/v1/runs/{ident}/evidence", dependencies=AUTH)
def evidence(ident: str, scenario_id: str | None = None):
    run = get_run(ident)
    steps = steps_for(ident)
    if scenario_id:
        if scenario_id not in {s["id"] for s in run.snapshot["plan"]["scenarios"]}:
            raise HTTPException(404, "场景不存在。")
        steps = {k: s for k, s in steps.items() if k.endswith(":" + scenario_id)}
    return {"steps": steps}


@app.get("/api/v1/runs/{ident}/export", dependencies=AUTH)
def export(ident: str):
    report = report_for(get_run(ident))
    report["audit_steps"] = steps_for(ident)
    return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="beseen-{ident[:8]}.json"'})
