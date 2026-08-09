from __future__ import annotations

from datetime import datetime

from .base import JobPosting, get_json

API = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyConnector:
    ats_type = "ashby"

    async def fetch(self, board_token: str) -> list[JobPosting]:
        resp = await get_json(f"{API}/{board_token}", params={"includeCompensation": "true"})
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        postings = []
        for j in jobs:
            if not j.get("isListed", True):
                continue
            posted_at = None
            if j.get("publishedAt"):
                try:
                    posted_at = datetime.fromisoformat(j["publishedAt"].replace("Z", "+00:00"))
                except ValueError:
                    pass
            postings.append(
                JobPosting(
                    external_id=str(j["id"]),
                    title=j.get("title", ""),
                    url=j.get("jobUrl", ""),
                    apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
                    location=j.get("location"),
                    department=j.get("department") or j.get("team"),
                    description_md=j.get("descriptionPlain") or "",
                    posted_at=posted_at,
                    raw={k: v for k, v in j.items() if k != "descriptionHtml"},
                )
            )
        return postings

    async def board_exists(self, candidate_token: str) -> bool:
        try:
            resp = await get_json(f"{API}/{candidate_token}")
        except Exception:
            return False
        if resp.status_code != 200:
            return False
        data = resp.json()
        # Ashby returns {"jobs": []} even for some unknown boards only when valid;
        # unknown boards give errors/nulls.
        return isinstance(data, dict) and isinstance(data.get("jobs"), list)
