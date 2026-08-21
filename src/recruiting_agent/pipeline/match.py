from __future__ import annotations

import asyncio
import json

import typer
from sqlmodel import select

from ..agents.llm import track_usage
from ..agents.prefilter import PrefilterInput, prefilter_batch
from ..agents.profile import load_profile
from ..agents.scoring import PROMPT_VERSION, score_job
from ..db import get_session, init_db
from ..models import (
    Company,
    Job,
    Match,
    Prefilter,
    PrefilterVerdict,
    Recommendation,
    RunKind,
)
from ..settings import settings

#: Rows written to the database between commits, so a long run checkpoints as it goes.
COMMIT_EVERY = 20


async def run_match(limit: int | None = None, rescore: bool = False) -> dict:
    from .runs import track_run

    init_db()
    profile = load_profile()
    batch_size = settings.prefilter_batch_size
    totals = {
        "prefiltered": 0,
        "plausible": 0,
        "rejected": 0,
        "scored": 0,
        "score_errors": 0,
        "cost_usd": 0.0,
    }

    with get_session() as session, track_usage() as usage:
        with track_run(session, RunKind.match) as handle:
            companies = {c.id: c.name for c in session.exec(select(Company)).all()}
            active_jobs = session.exec(select(Job).where(Job.is_active == True)).all()  # noqa: E712

            # Stage 1: cheap prefilter for jobs without a verdict for this (content, profile) version.
            evaluated = set()
            if not rescore:
                for p in session.exec(
                    select(Prefilter).where(Prefilter.profile_hash == profile.hash)
                ).all():
                    evaluated.add((p.job_id, p.content_hash))
            todo = [j for j in active_jobs if (j.id, j.content_hash) not in evaluated]
            if limit:
                todo = todo[:limit]

            typer.echo(f"Prefiltering {len(todo)} jobs (batches of {batch_size})…")
            batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
            prefilter_semaphore = asyncio.Semaphore(settings.prefilter_concurrency)

            async def prefilter_one(batch: list[Job]) -> tuple[list[Job], dict | None]:
                inputs = [
                    PrefilterInput(
                        job_id=j.id,
                        title=j.title,
                        company=companies.get(j.company_id, "?"),
                        department=j.department,
                        location=j.location,
                    )
                    for j in batch
                ]
                async with prefilter_semaphore:
                    try:
                        return batch, await prefilter_batch(profile, inputs)
                    except Exception as exc:
                        typer.echo(f"  ✗ prefilter batch failed: {exc}")
                        return batch, None

            batches_done = 0
            group_size = settings.prefilter_concurrency * 2
            for group_start in range(0, len(batches), group_size):
                group = batches[group_start : group_start + group_size]
                results = await asyncio.gather(*(prefilter_one(b) for b in group))
                for batch, verdicts in results:
                    batches_done += 1
                    if verdicts is None:
                        continue
                    for j in batch:
                        verdict, reason = verdicts[j.id]
                        session.add(
                            Prefilter(
                                job_id=j.id,
                                content_hash=j.content_hash,
                                profile_hash=profile.hash,
                                verdict=PrefilterVerdict(verdict),
                                reason=reason,
                                model=settings.prefilter_model,
                            )
                        )
                        totals["prefiltered"] += 1
                        totals[verdict if verdict == "rejected" else "plausible"] += 1
                session.commit()
                typer.echo(
                    f"  batch {batches_done}/{len(batches)} "
                    f"({totals['plausible']} plausible so far)"
                )

            # Stage 2: full scoring for plausible jobs not yet scored for this version.
            scored = set()
            if not rescore:
                for m in session.exec(
                    select(Match).where(Match.profile_hash == profile.hash)
                ).all():
                    scored.add((m.job_id, m.content_hash))

            plausible_ids = {
                p.job_id
                for p in session.exec(
                    select(Prefilter).where(
                        Prefilter.profile_hash == profile.hash,
                        Prefilter.verdict == PrefilterVerdict.plausible,
                    )
                ).all()
            }
            to_score = [
                j
                for j in active_jobs
                if j.id in plausible_ids and (j.id, j.content_hash) not in scored
            ]
            typer.echo(f"Scoring {len(to_score)} plausible jobs…")

            semaphore = asyncio.Semaphore(settings.scoring_concurrency)

            async def score_one(job: Job) -> tuple[Job, object | None]:
                async with semaphore:
                    try:
                        result = await score_job(
                            profile,
                            company=companies.get(job.company_id, "?"),
                            title=job.title,
                            location=job.location,
                            department=job.department,
                            description_md=job.description_md,
                            metadata={"job_id": job.id, "company": companies.get(job.company_id)},
                        )
                        return job, result
                    except Exception as exc:
                        typer.echo(f"  ✗ {job.title}: {exc}")
                        return job, None

            done = 0
            for chunk_start in range(0, len(to_score), COMMIT_EVERY):
                chunk = to_score[chunk_start : chunk_start + COMMIT_EVERY]
                results = await asyncio.gather(*(score_one(j) for j in chunk))
                for job, result in results:
                    done += 1
                    if result is None:
                        totals["score_errors"] += 1
                        continue
                    data = result.data
                    session.add(
                        Match(
                            job_id=job.id,
                            content_hash=job.content_hash,
                            profile_hash=profile.hash,
                            score=data["score"],
                            recommendation=Recommendation(data["recommendation"]),
                            reasoning=data["reasoning"],
                            red_flags=json.dumps(data.get("red_flags", [])),
                            seniority_fit=data.get("seniority_fit", ""),
                            location_fit=data.get("location_fit", ""),
                            model=settings.scoring_model,
                            prompt_version=PROMPT_VERSION,
                            langfuse_trace_id=result.trace_id,
                            cost_usd=result.cost_usd,
                        )
                    )
                    totals["scored"] += 1
                    totals["cost_usd"] += result.cost_usd or 0
                    if data["score"] >= 75:
                        typer.echo(
                            f"  ★ {data['score']} {companies.get(job.company_id)} — {job.title}"
                        )
                session.commit()
                typer.echo(f"  {done}/{len(to_score)} scored")

            totals["cost_usd"] = round(totals["cost_usd"], 6)
            totals.update(usage.as_dict())
            handle.stats = totals

    cached = usage.cache_read_tokens
    billed = usage.input_tokens + cached + usage.cache_creation_tokens
    if billed:
        typer.echo(
            f"Match done: {totals['scored']} scored, {totals['prefiltered']} screened, "
            f"${usage.cost_usd:.4f} across {usage.llm_calls} calls "
            f"({cached / billed:.0%} of prompt tokens served from cache)"
        )
    else:
        typer.echo(f"Match done: {totals['scored']} scored, {totals['prefiltered']} screened")
    return totals
