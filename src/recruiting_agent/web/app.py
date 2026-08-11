from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import func, select

from ..db import get_session, get_setting, init_db, set_setting
from ..models import (
    Company,
    CompanyStatus,
    Feedback,
    Job,
    JobReview,
    Match,
    Prefilter,
    PrefilterVerdict,
    Run,
    utcnow,
)
from ..workspace import OnboardingStage, workspace

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Recruiting Agent", lifespan=lifespan)


@app.middleware("http")
async def require_onboarding(request: Request, call_next):
    if not workspace.is_ready and not request.url.path.startswith("/onboarding"):
        return RedirectResponse("/onboarding", status_code=303)
    return await call_next(request)


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, template, ctx)


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding(request: Request):
    if workspace.is_ready:
        return RedirectResponse("/", status_code=303)
    state = workspace.load_state()
    question = state.get("current_question")
    if state["stage"] == OnboardingStage.interview.value and not question:
        from ..agents.interview import OnboardingInterviewer

        question = await OnboardingInterviewer(workspace).next_question()
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


@app.post("/onboarding/resume")
async def onboarding_resume(resume: UploadFile = File(...)):
    workspace.store_resume(resume.filename or "resume.pdf", await resume.read())
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/interview")
def onboarding_interview(question: str = Form(...), answer: str = Form(...)):
    workspace.record_answer(question, answer)
    return RedirectResponse("/onboarding", status_code=303)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.post("/onboarding/companies")
def onboarding_companies(
    scope: str = Form(...),
    excited: str = Form(""),
    acceptable: str = Form(""),
    excluded: str = Form(""),
):
    workspace.save_company_calibration(
        scope=scope,
        excited=_csv(excited),
        acceptable=_csv(acceptable),
        excluded=_csv(excluded),
    )
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/roles")
def onboarding_roles(
    target_roles: str = Form(...),
    excited_examples: str = Form(""),
    pass_examples: str = Form(""),
):
    workspace.save_role_calibration(
        target_roles=_csv(target_roles),
        excited_examples=excited_examples,
        pass_examples=pass_examples,
    )
    return RedirectResponse("/onboarding", status_code=303)


@app.post("/onboarding/activate")
def onboarding_activate():
    workspace.activate()
    from ..seed import seed_companies

    with get_session() as session:
        seed_companies(session)
    return RedirectResponse("/", status_code=303)


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
    """Latest match per job, joined with job + company, filtered and sorted by score."""
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
        if len(out) >= limit:
            break
    return out


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
