from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import func, select

from ..db import get_session, get_setting, init_db, set_setting
from ..models import (
    Company,
    CompanyStatus,
    AgentAction,
    AgentCycle,
    AgentEvent,
    Feedback,
    Job,
    JobReview,
    JobSource,
    Match,
    MemoryRevision,
    Prefilter,
    PrefilterVerdict,
    Run,
    utcnow,
)
from ..workspace import OnboardingStage, workspace

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Recruiting Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def require_onboarding(request: Request, call_next):
    public_path = request.url.path.startswith(("/onboarding", "/static"))
    if not workspace.is_ready and not public_path:
        return RedirectResponse("/onboarding", status_code=303)
    return await call_next(request)


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, template, ctx)


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding(request: Request, background_tasks: BackgroundTasks):
    if workspace.is_ready:
        return RedirectResponse("/", status_code=303)
    state = workspace.load_state()
    question = state.get("current_question")
    generation = state.get("generation", {})
    started_at = generation.get("started_at")
    stale = False
    if started_at:
        try:
            stale = datetime.fromisoformat(started_at) < datetime.now(timezone.utc) - timedelta(seconds=60)
        except ValueError:
            stale = True
    if (
        state["stage"] == OnboardingStage.interview.value
        and not question
        and (generation.get("status") in {"pending", "failed", "idle"} or stale)
    ):
        _queue_interview(background_tasks)
        state = workspace.load_state()
    return render(
        request,
        "onboarding.html",
        active_page="onboarding",
        stage=state["stage"],
        state=state,
        question=question,
        stages=OnboardingStage,
    )


async def _prepare_interview_question() -> None:
    from ..agents.interview import OnboardingInterviewer

    try:
        await OnboardingInterviewer(workspace).next_question()
    except Exception as exc:
        state = workspace.load_state()
        state["generation"] = {
            "status": "failed",
            "message": f"The agent could not prepare the next question: {str(exc)[:180]}",
        }
        workspace.save_state(state)


def _queue_interview(background_tasks: BackgroundTasks) -> None:
    state = workspace.load_state()
    state["generation"] = {
        "status": "running",
        "message": state.get("generation", {}).get("message") or "Checking for a high-value clarification…",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    workspace.save_state(state)
    background_tasks.add_task(_prepare_interview_question)


@app.get("/onboarding/status")
def onboarding_status():
    state = workspace.load_state()
    return JSONResponse(
        {
            "stage": state.get("stage"),
            "generation": state.get("generation", {}),
            "has_question": bool(state.get("current_question")),
        }
    )


@app.post("/onboarding/resume")
async def onboarding_resume(resume: UploadFile = File(...)):
    workspace.store_resume(resume.filename or "resume.pdf", await resume.read())
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/brief")
def onboarding_brief(
    background_tasks: BackgroundTasks,
    role_thesis: str = Form(...),
    work_style: str = Form(""),
    locations: str = Form(...),
    remote_policy: str = Form("onsite_or_hybrid"),
    company_stages: str = Form(""),
    company_preferences: str = Form(""),
    verticals: str = Form(""),
    excluded_verticals: str = Form(""),
    stretch: str = Form("balanced"),
    hard_exclusions: str = Form(""),
    weekly_cadence: str = Form("10"),
    company_scope: str = Form("exploratory"),
):
    workspace.save_search_brief(
        {
            "role_thesis": role_thesis,
            "work_style": work_style,
            "locations": _csv(locations),
            "remote_policy": remote_policy,
            "company_stages": _csv(company_stages),
            "company_preferences": company_preferences,
            "verticals": _csv(verticals),
            "excluded_verticals": _csv(excluded_verticals),
            "stretch": stretch,
            "hard_exclusions": hard_exclusions,
            "weekly_cadence": weekly_cadence,
            "company_scope": company_scope,
        }
    )
    _queue_interview(background_tasks)
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/interview")
def onboarding_interview(
    background_tasks: BackgroundTasks,
    question: str = Form(...),
    topic: str = Form("clarification"),
    answer: str = Form(...),
):
    state = workspace.record_answer(question, answer, topic)
    if state.get("stage") == OnboardingStage.interview.value:
        _queue_interview(background_tasks)
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/interview/finish")
def onboarding_interview_finish():
    workspace.finish_interview()
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/edit/brief")
def onboarding_edit_brief():
    state = workspace.load_state()
    state["stage"] = OnboardingStage.brief.value
    workspace.save_state(state)
    return RedirectResponse("/onboarding", status_code=303)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.post("/onboarding/companies")
def onboarding_companies(
    scope: str = Form(...),
    included: list[str] = Form([]),
    priority: list[str] = Form([]),
    additions: str = Form(""),
):
    workspace.save_company_selection(
        scope=scope,
        included=included,
        priority=priority,
        additions=_csv(additions),
    )
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/roles")
async def onboarding_roles(request: Request):
    form = await request.form()
    ratings = {key.removeprefix("rating_"): str(value) for key, value in form.items() if key.startswith("rating_")}
    notes = {key.removeprefix("note_"): str(value) for key, value in form.items() if key.startswith("note_")}
    workspace.save_role_ratings(ratings, notes)
    return RedirectResponse("/onboarding", status_code=303)


async def _run_initial_cycle() -> None:
    from ..coordinator import SearchCoordinator

    await SearchCoordinator(candidate_workspace=workspace).run_cycle("onboarding_activation")


@app.post("/onboarding/activate")
def onboarding_activate(background_tasks: BackgroundTasks):
    workspace.activate()
    from ..seed import sync_candidate_companies

    with get_session() as session:
        sync_candidate_companies(session, workspace)
    background_tasks.add_task(_run_initial_cycle)
    return RedirectResponse("/getting-started", status_code=303)


@app.get("/getting-started", response_class=HTMLResponse)
def getting_started(request: Request):
    with get_session() as session:
        cycle = session.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc())).first()
        actions = (
            session.exec(select(AgentAction).where(AgentAction.cycle_id == cycle.id).order_by(AgentAction.id)).all()
            if cycle
            else []
        )
        jobs_count = session.exec(select(func.count()).select_from(Job)).one()
    return render(
        request,
        "getting_started.html",
        active_page="overview",
        cycle=cycle,
        actions=actions,
        jobs_count=jobs_count,
    )


@app.get("/getting-started/status")
def getting_started_status():
    with get_session() as session:
        cycle = session.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc())).first()
        actions = (
            session.exec(select(AgentAction).where(AgentAction.cycle_id == cycle.id).order_by(AgentAction.id)).all()
            if cycle
            else []
        )
        jobs_count = session.exec(select(func.count()).select_from(Job)).one()
    return JSONResponse(
        {
            "cycle": {"status": cycle.status.value, "phase": cycle.phase, "error": cycle.error} if cycle else None,
            "actions": [{"kind": item.kind, "status": item.status.value, "error": item.error} for item in actions],
            "jobs": jobs_count,
        }
    )


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    with get_session() as s:
        companies_active = s.exec(
            select(func.count()).select_from(Company).where(Company.status == CompanyStatus.active)
        ).one()
        pending = s.exec(
            select(Company).where(Company.status == CompanyStatus.pending_approval)
        ).all()
        jobs_active = s.exec(
            select(func.count()).select_from(Job).where(Job.is_active == True)  # noqa: E712
        ).one()
        prefiltered = s.exec(select(func.count()).select_from(Prefilter)).one()
        plausible = s.exec(
            select(func.count())
            .select_from(Prefilter)
            .where(Prefilter.verdict == PrefilterVerdict.plausible)
        ).one()
        scored = s.exec(select(func.count()).select_from(Match)).one()
        top = _match_rows(s, min_score=75, limit=15, status="new")
        runs = s.exec(select(Run).order_by(Run.started_at.desc()).limit(8)).all()
        run_rows = [(r, json.loads(r.stats_json or "{}")) for r in runs]
    return render(
        request,
        "overview.html",
        active_page="overview",
        companies_active=companies_active,
        pending=pending,
        jobs_active=jobs_active,
        prefiltered=prefiltered,
        plausible=plausible,
        scored=scored,
        top=top,
        runs=run_rows,
    )


def _match_rows(s, min_score: int = 0, limit: int = 500, status: str = "all", rec: str = "all"):
    """Latest active-harness match per job, filtered and sorted by score descending."""
    query = (
        select(Match, Job, Company)
        .join(Job, Match.job_id == Job.id)
        .join(Company, Job.company_id == Company.id)
        .where(Job.is_active == True)  # noqa: E712
        .order_by(Match.created_at.desc())
    )
    rows = s.exec(query).all()
    reviews = {review.job_id: review for review in s.exec(select(JobReview)).all()}
    seen: set[int] = set()
    out = []
    for match, job, company in rows:
        if workspace.is_ready and match.profile_hash != workspace.harness_hash:
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        if match.score < min_score:
            continue
        review = reviews.get(job.id)
        review_status = review.user_status if review else None
        match.user_status = review_status
        if status == "new" and review_status is not None:
            continue
        if status not in ("all", "new") and review_status != status:
            continue
        if rec != "all" and match.recommendation != rec:
            continue
        out.append((match, job, company, json.loads(match.red_flags or "[]")))
    out.sort(key=lambda row: (row[0].score, row[0].created_at), reverse=True)
    return out[:limit]


@app.get("/matches", response_class=HTMLResponse)
def matches(request: Request, min_score: int | None = None, status: str = "new", rec: str = "all"):
    with get_session() as s:
        if min_score is None:
            min_score = int(get_setting(s, "min_score_display") or 60)
        rows = _match_rows(s, min_score=min_score, status=status, rec=rec)
    return render(
        request,
        "matches.html",
        active_page="matches",
        rows=rows,
        min_score=min_score,
        status=status,
        rec=rec,
        harness_hash=workspace.harness_hash if workspace.is_ready else "",
    )


@app.post("/matches/{match_id}/status", response_class=HTMLResponse)
def set_match_status(request: Request, match_id: int, user_status: str = Form(...)):
    with get_session() as s:
        match = s.get(Match, match_id)
        if match:
            review = s.exec(select(JobReview).where(JobReview.job_id == match.job_id)).first()
            if review is None:
                review = JobReview(job_id=match.job_id)
            review.user_status = None if user_status == "new" else user_status
            review.updated_at = utcnow()
            s.add(review)
            s.commit()
            match.user_status = review.user_status
    return HTMLResponse(
        templates.get_template("partials/match_status.html").render(match=match)
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request, company_id: int | None = None, q: str = ""):
    with get_session() as s:
        query = (
            select(Job, Company)
            .join(Company, Job.company_id == Company.id)
            .where(Job.is_active == True)  # noqa: E712
            .order_by(Job.first_seen_at.desc())
        )
        if company_id:
            query = query.where(Job.company_id == company_id)
        if q:
            query = query.where(Job.title.contains(q))
        rows = s.exec(query.limit(1000)).all()
        companies = s.exec(select(Company).order_by(Company.name)).all()
    return render(
        request,
        "jobs.html",
        active_page="jobs",
        rows=rows,
        companies=companies,
        company_id=company_id,
        q=q,
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    with get_session() as s:
        job = s.get(Job, job_id)
        company = s.get(Company, job.company_id)
        match_query = select(Match).where(Match.job_id == job_id)
        if workspace.is_ready:
            match_query = match_query.where(Match.profile_hash == workspace.harness_hash)
        match = s.exec(match_query.order_by(Match.created_at.desc())).first()
        review = s.exec(select(JobReview).where(JobReview.job_id == job_id)).first()
        if match:
            match.user_status = review.user_status if review else None
        prefilter = s.exec(
            select(Prefilter)
            .where(Prefilter.job_id == job_id)
            .order_by(Prefilter.created_at.desc())
        ).first()
        red_flags = json.loads(match.red_flags or "[]") if match else []
    return render(
        request,
        "job_detail.html",
        active_page="jobs",
        job=job,
        company=company,
        match=match,
        prefilter=prefilter,
        red_flags=red_flags,
    )


@app.post("/jobs/{job_id}/feedback")
def job_feedback(job_id: int, label: str = Form(...), reason: str = Form("")):
    with get_session() as s:
        if s.get(Job, job_id) is not None:
            s.add(Feedback(job_id=job_id, label=label, reason=reason.strip()))
            s.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request):
    with get_session() as s:
        rows = []
        for c in s.exec(select(Company).order_by(Company.name)).all():
            job_count = s.exec(
                select(func.count())
                .select_from(Job)
                .where(Job.company_id == c.id, Job.is_active == True)  # noqa: E712
            ).one()
            rows.append((c, job_count))
        pending = [c for c, _ in rows if c.status == CompanyStatus.pending_approval]
    return render(
        request, "companies.html", active_page="companies", rows=rows, pending=pending
    )


@app.post("/companies/{company_id}/decision")
def company_decision(company_id: int, decision: str = Form(...)):
    with get_session() as s:
        c = s.get(Company, company_id)
        if c and decision in ("approve", "reject", "pause", "activate"):
            c.status = {
                "approve": CompanyStatus.active,
                "activate": CompanyStatus.active,
                "reject": CompanyStatus.rejected,
                "pause": CompanyStatus.paused,
            }[decision]
            s.add(c)
            s.commit()
    return RedirectResponse("/companies", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    with get_session() as s:
        rows = s.exec(select(Run).order_by(Run.started_at.desc()).limit(100)).all()
        run_rows = [(r, json.loads(r.stats_json or "{}")) for r in rows]
    return render(request, "runs.html", active_page="runs", runs=run_rows)


ACTIVITY_PHASES = [
    ("probe", "Verify sources", "Check which career systems can be trusted."),
    ("ingest", "Collect openings", "Pull current roles from approved companies."),
    ("match", "Evaluate roles", "Apply the active search policy and evidence rubric."),
    ("scout", "Scout gaps", "Inspect unsupported career sites for missing coverage."),
    ("consolidate_memory", "Update memory", "Turn durable observations into a reviewable revision."),
    ("digest", "Prepare briefing", "Summarize what changed and what needs attention."),
]


def _activity_json(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"result": raw}
    return value if isinstance(value, dict) else {"result": value}


def _activity_status(value) -> str:
    return getattr(value, "value", str(value))


def _activity_duration(action: AgentAction) -> str:
    now = datetime.now(timezone.utc)
    if action.created_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    seconds = max(0, int(((action.finished_at or now) - action.created_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _activity_action_view(action: AgentAction, *, collected_roles: int = 0) -> dict:
    payload = _activity_json(action.result_json)
    status = _activity_status(action.status)
    definitions = {kind: (title, detail) for kind, title, detail in ACTIVITY_PHASES}
    title, detail = definitions.get(
        action.kind,
        (action.kind.replace("_", " ").title(), "A persisted agent action."),
    )
    if status == "running":
        if action.kind == "match" and collected_roles:
            summary = f"Evaluating {collected_roles:,} newly collected roles"
        else:
            summary = f"{detail.rstrip('.')} now"
    elif status == "failed":
        summary = action.error or "The action stopped before completing."
    elif action.kind == "ingest" and payload:
        summary = (
            f"{int(payload.get('new', 0)):,} new roles across "
            f"{int(payload.get('companies', 0)):,} companies"
        )
        if payload.get("errors"):
            summary += f" · {payload['errors']} source errors"
    elif action.kind == "probe":
        summary = "Career sources verified and ready for collection"
    elif action.kind == "match" and payload:
        evaluated = payload.get("evaluated") or payload.get("scored") or payload.get("jobs")
        summary = f"{int(evaluated):,} roles evaluated" if evaluated else "Role evaluation complete"
    elif payload:
        summary = str(payload.get("summary") or payload.get("status") or detail)
    else:
        summary = detail
    return {
        "id": action.id,
        "cycle_id": action.cycle_id,
        "kind": action.kind,
        "title": title,
        "detail": detail,
        "status": status,
        "summary": summary,
        "attempts": action.attempts,
        "duration": _activity_duration(action),
        "created_at": action.created_at,
        "finished_at": action.finished_at,
        "payload": payload,
        "payload_pretty": json.dumps(payload, indent=2, sort_keys=True, default=str),
        "error": action.error,
    }


def _activity_signature(cycle: AgentCycle | None, actions: list[AgentAction], events: list[AgentEvent]) -> str:
    cycle_state = "none" if cycle is None else f"{cycle.id}:{_activity_status(cycle.status)}:{cycle.phase}"
    action_state = ",".join(
        f"{action.id}:{_activity_status(action.status)}:{action.finished_at or ''}" for action in actions[:12]
    )
    event_state = str(events[0].id) if events else "0"
    return f"{cycle_state}|{action_state}|{event_state}"


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    with get_session() as s:
        cycles = s.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc()).limit(50)).all()
        actions = s.exec(select(AgentAction).order_by(AgentAction.created_at.desc()).limit(100)).all()
        events = s.exec(select(AgentEvent).order_by(AgentEvent.created_at.desc()).limit(100)).all()
    latest_cycle = cycles[0] if cycles else None
    ingest_payload = next(
        (_activity_json(action.result_json) for action in actions if action.kind == "ingest" and _activity_status(action.status) == "success"),
        {},
    )
    collected_roles = int(ingest_payload.get("new", 0) or 0)
    action_views = [_activity_action_view(action, collected_roles=collected_roles) for action in actions]
    current_action = next((action for action in action_views if action["status"] == "running"), None)
    if current_action is None and action_views:
        current_action = action_views[0]
    latest_actions = {action["kind"]: action for action in action_views if latest_cycle and action["cycle_id"] == latest_cycle.id}
    phase_views = []
    for kind, title, detail in ACTIVITY_PHASES:
        action = latest_actions.get(kind)
        phase_views.append(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
                "status": action["status"] if action else "pending",
                "summary": action["summary"] if action else "Queued",
            }
        )
    event_views = []
    for event in events:
        payload = _activity_json(event.payload_json)
        event_views.append(
            {
                "id": event.id,
                "type": event.event_type,
                "label": event.event_type.replace("_", " ").title(),
                "source": event.source,
                "confidence": event.confidence,
                "cycle_id": event.cycle_id,
                "harness_hash": event.harness_hash,
                "created_at": event.created_at,
                "payload_pretty": json.dumps(payload, indent=2, sort_keys=True, default=str),
            }
        )
    completed_count = sum(action["status"] == "success" for action in action_views)
    failed_count = sum(action["status"] == "failed" for action in action_views)
    return render(
        request,
        "activity.html",
        active_page="activity",
        cycles=cycles,
        actions=action_views,
        events=event_views,
        latest_cycle=latest_cycle,
        current_action=current_action,
        phases=phase_views,
        completed_count=completed_count,
        failed_count=failed_count,
        collected_roles=collected_roles,
        activity_signature=_activity_signature(latest_cycle, actions, events),
    )


@app.get("/activity/status")
def activity_status():
    with get_session() as s:
        cycle = s.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc()).limit(1)).first()
        actions = s.exec(select(AgentAction).order_by(AgentAction.created_at.desc()).limit(12)).all()
        events = s.exec(select(AgentEvent).order_by(AgentEvent.created_at.desc()).limit(1)).all()
    running = next((action for action in actions if _activity_status(action.status) == "running"), None)
    return {
        "signature": _activity_signature(cycle, actions, events),
        "status": _activity_status(cycle.status) if cycle else "idle",
        "phase": cycle.phase if cycle else "waiting",
        "phase_label": (
            next((title for kind, title, _ in ACTIVITY_PHASES if running and kind == running.kind), None)
            or (cycle.phase.replace("_", " ").title() if cycle else "Waiting for first cycle")
        ),
    }


@app.get("/search-state", response_class=HTMLResponse)
def search_state(request: Request):
    sections = {
        "Search constitution": workspace.read_section("policy/search-constitution.md"),
        "Company thesis": workspace.read_section("policy/company-thesis.md"),
        "Current search state": workspace.read_section("memory/current-search-state.md"),
    }
    return render(
        request,
        "search_state.html",
        active_page="search_state",
        sections=sections,
        harness_hash=workspace.harness_hash,
    )


@app.get("/memory", response_class=HTMLResponse)
def memory(request: Request):
    with get_session() as s:
        revisions = s.exec(select(MemoryRevision).order_by(MemoryRevision.created_at.desc())).all()
    return render(request, "memory.html", active_page="memory", revisions=revisions)


@app.post("/memory/{revision_id}/decision")
def memory_decision(revision_id: int, decision: str = Form(...)):
    if decision not in {"approve", "reject"}:
        return RedirectResponse("/memory", status_code=303)
    with get_session() as s:
        revision = s.get(MemoryRevision, revision_id)
        if revision and revision.status == "proposed":
            if decision == "approve":
                workspace.apply_approved_revision(revision.section, revision.content)
                revision.status = "approved"
            else:
                revision.status = "rejected"
            revision.decided_at = utcnow()
            s.add(revision)
            s.commit()
    return RedirectResponse("/memory", status_code=303)


@app.get("/feedback", response_class=HTMLResponse)
def feedback(request: Request):
    with get_session() as s:
        rows = s.exec(
            select(Feedback, Job, Company)
            .join(Job, Feedback.job_id == Job.id)
            .join(Company, Job.company_id == Company.id)
            .order_by(Feedback.created_at.desc())
        ).all()
    return render(request, "feedback.html", active_page="feedback", rows=rows)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with get_session() as s:
        companies = s.exec(select(Company).order_by(Company.name)).all()
        by_company: dict[int, list[JobSource]] = {}
        for source in s.exec(select(JobSource)).all():
            by_company.setdefault(source.company_id, []).append(source)
    return render(
        request,
        "sources.html",
        active_page="sources",
        companies=companies,
        by_company=by_company,
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    with get_session() as s:
        min_score_display = get_setting(s, "min_score_display")
    return render(
        request,
        "settings.html",
        active_page="settings",
        min_score_display=min_score_display,
    )


@app.post("/settings")
def settings_save(min_score_display: str = Form(...)):
    with get_session() as s:
        set_setting(s, "min_score_display", min_score_display)
    return RedirectResponse("/settings", status_code=303)
