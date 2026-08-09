from __future__ import annotations

from datetime import datetime

from markdownify import markdownify

from .base import JobPosting, get_json

API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseConnector:
    ats_type = "greenhouse"

    async def fetch(self, board_token: str) -> list[JobPosting]:
        resp = await get_json(f"{API}/{board_token}/jobs", params={"content": "true"})
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        postings = []
        for j in jobs:
            posted_at = None
            if j.get("updated_at"):
                try:
                    posted_at = datetime.fromisoformat(j["updated_at"])
                except ValueError:
                    pass
            departments = [d["name"] for d in j.get("departments", []) if d.get("name")]
            postings.append(
                JobPosting(
                    external_id=str(j["id"]),
                    title=j.get("title", ""),
                    url=j.get("absolute_url", ""),
                    apply_url=j.get("absolute_url", ""),
                    location=(j.get("location") or {}).get("name"),
                    department=", ".join(departments) or None,
                    description_md=markdownify(j.get("content") or "", heading_style="ATX"),
                    posted_at=posted_at,
                    raw=j,
                )
            )
        return postings

    async def board_exists(self, candidate_token: str) -> bool:
        try:
            resp = await get_json(f"{API}/{candidate_token}/jobs")
        except Exception:
            return False
        return resp.status_code == 200 and "jobs" in resp.json()
