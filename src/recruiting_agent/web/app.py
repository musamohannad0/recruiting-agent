from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
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
from ..reporting import (
    ACTION_KINDS,
    action_title,
    elapsed_seconds,
    format_duration,
    format_money,
    humanize_key,
    parse_payload,
    payload_facts,
    render_markdown,
    summarize_action,
)
from ..workspace import OnboardingStage, workspace

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# `| markdown` is safe to mark safe: render_markdown escapes embedded raw HTML.
templates.env.filters["markdown"] = render_markdown


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


def _boot_state() -> tuple[AgentCycle | None, list[AgentAction], int, dict]:
    with get_session() as session:
        cycle = session.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc())).first()
        actions = (
            session.exec(
                select(AgentAction).where(AgentAction.cycle_id == cycle.id).order_by(AgentAction.id)
            ).all()
            if cycle
            else []
        )
        jobs_count = session.exec(select(func.count()).select_from(Job)).one()
    payload = {
        "cycle": (
            {"status": _activity_status(cycle.status), "phase": cycle.phase, "error": cycle.error}
            if cycle
            else None
        ),
        "actions": [
            {"kind": item.kind, "status": _activity_status(item.status), "error": item.error}
            for item in actions
        ],
        "jobs": jobs_count,
    }
    return cycle, actions, jobs_count, payload


@app.get("/getting-started", response_class=HTMLResponse)
def getting_started(request: Request):
    cycle, actions, jobs_count, payload = _boot_state()
    return render(
        request,
        "getting_started.html",
        active_page="overview",
        cycle=cycle,
        actions=actions,
        jobs_count=jobs_count,
        # Seed the poll with the state that was actually rendered; an empty signature
        # meant the first change after page load was recorded instead of shown.
        boot_signature=json.dumps(payload),
    )


@app.get("/getting-started/status")
def getting_started_status():
    return JSONResponse(_boot_state()[3])


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


@dataclass(frozen=True)
class MatchRow:
    """One ranked role, ready to render.

    ``review_status`` is carried here rather than assigned onto the ``Match``
    instance: triage state lives on ``JobReview``, and writing it onto a live ORM
    object risks the next flush persisting a display-only value.
    """

    match: Match
    job: Job
    company: Company
    red_flags: list[str]
    review_status: str | None


def _match_rows(
    s, min_score: int = 0, limit: int = 500, status: str = "all", rec: str = "all"
) -> list[MatchRow]:
    """Latest active-harness match per job, filtered and sorted by score descending.

    Ranking happens in SQL: the previous version loaded every match row ever written
    and de-duplicated in Python, so the page got slower with every re-score.
    """
    ranked = select(
        Match.id.label("match_id"),
        func.row_number()
        .over(
            partition_by=Match.job_id,
            order_by=(Match.created_at.desc(), Match.id.desc()),
        )
        .label("rank"),
    ).join(Job, Match.job_id == Job.id).where(Job.is_active == True)  # noqa: E712
    if workspace.is_ready:
        ranked = ranked.where(Match.profile_hash == workspace.harness_hash)
    latest = ranked.subquery()

    query = (
        select(Match, Job, Company, JobReview)
        .join(latest, latest.c.match_id == Match.id)
        .join(Job, Match.job_id == Job.id)
        .join(Company, Job.company_id == Company.id)
        .outerjoin(JobReview, JobReview.job_id == Job.id)
        .where(latest.c.rank == 1, Match.score >= min_score)
        .order_by(Match.score.desc(), Match.created_at.desc())
    )
    if status == "new":
        query = query.where(
            or_(JobReview.id.is_(None), JobReview.user_status.is_(None))
        )
    elif status != "all":
        query = query.where(JobReview.user_status == status)
    if rec != "all":
        query = query.where(Match.recommendation == rec)

    return [
        MatchRow(
            match=match,
            job=job,
            company=company,
            red_flags=_json_list(match.red_flags),
            review_status=review.user_status if review else None,
        )
        for match, job, company, review in s.exec(query.limit(limit)).all()
    ]


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else [value]


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


REVIEW_STATUSES = {"saved", "applied", "dismissed", "new"}


@app.post("/matches/{match_id}/status", response_class=HTMLResponse)
def set_match_status(request: Request, match_id: int, user_status: str = Form(...)):
    if user_status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown review status: {user_status}")
    resolved: str | None = None
    role_name = ""
    with get_session() as s:
        match = s.get(Match, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="No such evaluation")
        job = s.get(Job, match.job_id)
        company = s.get(Company, job.company_id) if job else None
        if job:
            role_name = f"{job.title} at {company.name}" if company else job.title
        review = s.exec(select(JobReview).where(JobReview.job_id == match.job_id)).first()
        if review is None:
            review = JobReview(job_id=match.job_id)
        review.user_status = None if user_status == "new" else user_status
        review.updated_at = utcnow()
        s.add(review)
        s.commit()
        resolved = review.user_status
    return HTMLResponse(
        templates.get_template("partials/match_status.html").render(
            match_id=match_id, user_status=resolved, role_name=role_name
        )
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
        if job is None:
            raise HTTPException(status_code=404, detail="No such role")
        company = s.get(Company, job.company_id)
        match_query = select(Match).where(Match.job_id == job_id)
        if workspace.is_ready:
            match_query = match_query.where(Match.profile_hash == workspace.harness_hash)
        match = s.exec(match_query.order_by(Match.created_at.desc())).first()
        review = s.exec(select(JobReview).where(JobReview.job_id == job_id)).first()
        prefilter = s.exec(
            select(Prefilter)
            .where(Prefilter.job_id == job_id)
            .order_by(Prefilter.created_at.desc())
        ).first()
        red_flags = _json_list(match.red_flags) if match else []
        feedback = s.exec(
            select(Feedback).where(Feedback.job_id == job_id).order_by(Feedback.created_at.desc())
        ).all()
    return render(
        request,
        "job_detail.html",
        active_page="jobs",
        job=job,
        company=company,
        match=match,
        prefilter=prefilter,
        red_flags=red_flags,
        review_status=review.user_status if review else None,
        feedback=feedback,
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
        # One grouped count rather than a COUNT per company.
        counts = dict(
            s.exec(
                select(Job.company_id, func.count(Job.id))
                .where(Job.is_active == True)  # noqa: E712
                .group_by(Job.company_id)
            ).all()
        )
        rows = [
            (company, counts.get(company.id, 0))
            for company in s.exec(select(Company).order_by(Company.name)).all()
        ]
        pending = [company for company, _ in rows if company.status == CompanyStatus.pending_approval]
        grouped = {
            "Active": [row for row in rows if row[0].status == CompanyStatus.active],
            "Paused": [row for row in rows if row[0].status == CompanyStatus.paused],
            "Rejected": [row for row in rows if row[0].status == CompanyStatus.rejected],
        }
    return render(
        request,
        "companies.html",
        active_page="companies",
        rows=rows,
        pending=pending,
        grouped={name: items for name, items in grouped.items() if items},
        tracked_roles=sum(count for _, count in rows),
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
    return render(
        request,
        "runs.html",
        active_page="runs",
        runs=[
            {
                "kind": humanize_key(run.kind),
                "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration": format_duration(elapsed_seconds(run.started_at, run.finished_at)),
                "facts": payload_facts(parse_payload(run.stats_json)),
                "error": run.error,
            }
            for run in rows
        ],
    )


def _activity_status(value) -> str:
    return getattr(value, "value", str(value))


def _action_view(action: AgentAction, *, collected_roles: int = 0) -> dict:
    """One agent action, described in words and labelled facts rather than JSON."""
    payload = parse_payload(action.result_json)
    status = _activity_status(action.status)
    title, detail = action_title(action.kind)
    return {
        "id": action.id,
        "cycle_id": action.cycle_id,
        "kind": action.kind,
        "title": title,
        "detail": detail,
        "status": status,
        "summary": summarize_action(
            action.kind, status, payload, action.error, {"collected_roles": collected_roles}
        ),
        "attempts": action.attempts,
        "duration": format_duration(elapsed_seconds(action.created_at, action.finished_at)),
        "created_at": action.created_at,
        "finished_at": action.finished_at,
        "facts": payload_facts(payload),
        "cost": format_money(action.cost_usd) if action.llm_calls else None,
        "llm_calls": action.llm_calls,
        "cached_tokens": action.cached_tokens,
        "token_summary": (
            f"{action.input_tokens + action.output_tokens:,} tokens"
            + (f" · {action.cached_tokens:,} from cache" if action.cached_tokens else "")
            if action.llm_calls
            else None
        ),
        "error": action.error,
    }


def _activity_signature(cycle: AgentCycle | None, actions: list[AgentAction], events: list[AgentEvent]) -> str:
    cycle_state = "none" if cycle is None else f"{cycle.id}:{_activity_status(cycle.status)}:{cycle.phase}"
    action_state = ",".join(
        f"{action.id}:{_activity_status(action.status)}:{action.finished_at or ''}" for action in actions[:12]
    )
    event_state = str(events[0].id) if events else "0"
    return f"{cycle_state}|{action_state}|{event_state}"


def _activity_context() -> dict:
    with get_session() as s:
        cycles = s.exec(select(AgentCycle).order_by(AgentCycle.started_at.desc()).limit(50)).all()
        actions = s.exec(select(AgentAction).order_by(AgentAction.created_at.desc()).limit(100)).all()
        events = s.exec(select(AgentEvent).order_by(AgentEvent.created_at.desc()).limit(100)).all()
        spend_7d = s.exec(
            select(func.coalesce(func.sum(AgentCycle.cost_usd), 0.0)).where(
                AgentCycle.started_at >= utcnow() - timedelta(days=7)
            )
        ).one()
    latest_cycle = cycles[0] if cycles else None
    collected_roles = next(
        (
            int(parse_payload(action.result_json).get("new", 0) or 0)
            for action in actions
            if action.kind == "ingest" and _activity_status(action.status) == "success"
        ),
        0,
    )
    action_views = [_action_view(action, collected_roles=collected_roles) for action in actions]
    current_action = next(
        (action for action in action_views if action["status"] == "running"),
        action_views[0] if action_views else None,
    )
    cycle_actions = {
        action["kind"]: action
        for action in action_views
        if latest_cycle and action["cycle_id"] == latest_cycle.id
    }
    # A cached action keeps the cycle_id of the run that actually did the work, so the
    # action rows alone make it look as though this cycle skipped the stage. The cycle's
    # own stats record every outcome, cached ones included — prefer that.
    recorded = parse_payload(latest_cycle.stats_json).get("actions", {}) if latest_cycle else {}
    phases = []
    for kind, (title, detail) in ACTION_KINDS.items():
        if kind == "digest":
            continue
        outcome = recorded.get(kind) if isinstance(recorded.get(kind), dict) else None
        action = cycle_actions.get(kind)
        if outcome and outcome.get("status") == "cached":
            payload = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
            status, duration = "cached", ""
            summary = f"Carried over — {summarize_action(kind, 'success', payload).lower()}"
        elif action:
            status, summary, duration = action["status"], action["summary"], action["duration"]
        elif outcome and outcome.get("status") == "failed":
            status, summary, duration = "failed", outcome.get("error", "The action failed."), ""
        else:
            status, summary, duration = "pending", "Not due this cycle", ""
        phases.append(
            {"kind": kind, "title": title, "detail": detail,
             "status": status, "summary": summary, "duration": duration}
        )
    return {
        "cycles": cycles,
        "actions": action_views,
        "events": [
            {
                "id": event.id,
                "label": humanize_key(event.event_type),
                "source": event.source,
                "cycle_id": event.cycle_id,
                "created_at": event.created_at,
                "summary": summarize_action(
                    event.event_type.removesuffix("_completed"),
                    "success",
                    parse_payload(event.payload_json),
                ),
                "facts": payload_facts(parse_payload(event.payload_json)),
            }
            for event in events
        ],
        "latest_cycle": latest_cycle,
        "current_action": current_action,
        "phases": phases,
        "completed_count": sum(action["status"] == "success" for action in action_views),
        "failed_count": sum(action["status"] == "failed" for action in action_views),
        "collected_roles": collected_roles,
        "cycle_cost": format_money(latest_cycle.cost_usd) if latest_cycle else "$0.00",
        "cycle_calls": latest_cycle.llm_calls if latest_cycle else 0,
        "cycle_duration": (
            format_duration(elapsed_seconds(latest_cycle.started_at, latest_cycle.finished_at))
            if latest_cycle
            else "—"
        ),
        "cycle_cache_ratio": _cache_ratio(latest_cycle),
        "spend_7d": format_money(spend_7d or 0.0),
        "activity_signature": _activity_signature(latest_cycle, actions, events),
    }


def _cache_ratio(cycle: AgentCycle | None) -> str:
    """How much of the last cycle's prompt was served from cache instead of re-sent."""
    if cycle is None or not cycle.llm_calls:
        return "—"
    billed = cycle.input_tokens + cycle.cached_tokens
    return f"{cycle.cached_tokens / billed:.0%}" if billed else "—"


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    context = _activity_context()
    # htmx swaps just the live region; a full navigation renders the whole page.
    template = "partials/activity_body.html" if request.headers.get("HX-Request") else "activity.html"
    return render(request, template, active_page="activity", **context)


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
            action_title(running.kind)[0]
            if running
            else (humanize_key(cycle.phase) if cycle else "Waiting for first cycle")
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


#: These tables only grow, so every list page needs a ceiling.
PAGE_LIMIT = 200


@app.get("/memory", response_class=HTMLResponse)
def memory(request: Request):
    with get_session() as s:
        revisions = s.exec(
            select(MemoryRevision)
            .order_by(MemoryRevision.created_at.desc())
            .limit(PAGE_LIMIT)
        ).all()
        total = s.exec(select(func.count()).select_from(MemoryRevision)).one()
    return render(
        request,
        "memory.html",
        active_page="memory",
        revisions=revisions,
        truncated=max(0, total - len(revisions)),
    )


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
            .limit(PAGE_LIMIT)
        ).all()
        total = s.exec(select(func.count()).select_from(Feedback)).one()
    return render(
        request,
        "feedback.html",
        active_page="feedback",
        rows=rows,
        truncated=max(0, total - len(rows)),
    )


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
