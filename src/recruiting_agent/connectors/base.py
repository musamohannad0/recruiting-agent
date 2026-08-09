from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

HTTP_TIMEOUT = httpx.Timeout(30.0)
USER_AGENT = "recruiting-agent/0.1 (personal job-search tool)"


@dataclass
class JobPosting:
    external_id: str
    title: str
    url: str
    apply_url: str | None = None
    location: str | None = None
    department: str | None = None
    description_md: str = ""
    posted_at: datetime | None = None
    raw: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = f"{self.title}\n{self.location or ''}\n{self.description_md}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Connector(Protocol):
    ats_type: str

    async def fetch(self, board_token: str) -> list[JobPosting]:
        """Fetch all open postings for a board. Raises on HTTP/parse errors."""
        ...

    async def board_exists(self, candidate_token: str) -> bool:
        """Whether this token resolves to a valid board (used by the probe)."""
        ...


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def get_json(url: str, params: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(url, params=params)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp
