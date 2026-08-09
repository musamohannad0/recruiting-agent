from __future__ import annotations

from datetime import datetime, timezone

from markdownify import markdownify

from .base import JobPosting, get_json

API = "https://api.lever.co/v0/postings"


class LeverConnector:
    ats_type = "lever"

    async def fetch(self, board_token: str) -> list[JobPosting]:
        resp = await get_json(f"{API}/{board_token}", params={"mode": "json"})
        resp.raise_for_status()
        postings = []
        for j in resp.json():
            categories = j.get("categories") or {}
            posted_at = None
            if j.get("createdAt"):
                posted_at = datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc)
            description_html = j.get("description") or ""
            for group in j.get("lists") or []:
                description_html += f"<h3>{group.get('text', '')}</h3>{group.get('content', '')}"
            postings.append(
                JobPosting(
                    external_id=str(j["id"]),
                    title=j.get("text", ""),
                    url=j.get("hostedUrl", ""),
                    apply_url=j.get("applyUrl") or (j.get("hostedUrl", "") + "/apply"),
                    location=categories.get("location"),
                    department=categories.get("team") or categories.get("department"),
                    description_md=markdownify(description_html, heading_style="ATX"),
                    posted_at=posted_at,
                    raw=j,
                )
            )
        return postings

    async def board_exists(self, candidate_token: str) -> bool:
        try:
            resp = await get_json(f"{API}/{candidate_token}", params={"mode": "json"})
        except Exception:
            return False
        return resp.status_code == 200 and isinstance(resp.json(), list)
