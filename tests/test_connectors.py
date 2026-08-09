import pytest
import respx
from httpx import Response

from recruiting_agent.connectors.ashby import AshbyConnector
from recruiting_agent.connectors.greenhouse import GreenhouseConnector
from recruiting_agent.connectors.lever import LeverConnector

GH_FIXTURE = {
    "jobs": [
        {
            "id": 123,
            "title": "Business Operations Lead",
            "updated_at": "2026-08-01T12:00:00-04:00",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/123",
            "departments": [{"name": "Operations"}],
            "content": "&lt;p&gt;Own pricing &amp; strategy&lt;/p&gt;",
        }
    ]
}

LEVER_FIXTURE = [
    {
        "id": "abc-def",
        "text": "Strategy & Ops Manager",
        "createdAt": 1754006400000,
        "categories": {"location": "San Francisco", "team": "GTM"},
        "hostedUrl": "https://jobs.lever.co/testco/abc-def",
        "applyUrl": "https://jobs.lever.co/testco/abc-def/apply",
        "description": "<p>Do strategy</p>",
        "lists": [{"text": "Requirements", "content": "<li>5 years</li>"}],
    }
]

ASHBY_FIXTURE = {
    "jobs": [
        {
            "id": "uuid-1",
            "title": "Product Operations",
            "isListed": True,
            "publishedAt": "2026-08-01T00:00:00Z",
            "location": "Remote (US)",
            "department": "Ops",
            "jobUrl": "https://jobs.ashbyhq.com/testco/uuid-1",
            "applyUrl": "https://jobs.ashbyhq.com/testco/uuid-1/application",
            "descriptionPlain": "Run product ops",
        },
        {"id": "uuid-2", "title": "Hidden role", "isListed": False},
    ]
}


@respx.mock
@pytest.mark.asyncio
async def test_greenhouse_fetch():
    respx.get("https://boards-api.greenhouse.io/v1/boards/testco/jobs").mock(
        return_value=Response(200, json=GH_FIXTURE)
    )
    jobs = await GreenhouseConnector().fetch("testco")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.external_id == "123"
    assert j.location == "New York, NY"
    assert j.department == "Operations"
    assert "pricing" in j.description_md


@respx.mock
@pytest.mark.asyncio
async def test_lever_fetch():
    respx.get("https://api.lever.co/v0/postings/testco").mock(
        return_value=Response(200, json=LEVER_FIXTURE)
    )
    jobs = await LeverConnector().fetch("testco")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Strategy & Ops Manager"
    assert j.apply_url.endswith("/apply")
    assert "Requirements" in j.description_md
    assert j.posted_at is not None


@respx.mock
@pytest.mark.asyncio
async def test_ashby_fetch_skips_unlisted():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/testco").mock(
        return_value=Response(200, json=ASHBY_FIXTURE)
    )
    jobs = await AshbyConnector().fetch("testco")
    assert len(jobs) == 1
    assert jobs[0].title == "Product Operations"


@respx.mock
@pytest.mark.asyncio
async def test_board_exists_false_on_404():
    respx.get("https://boards-api.greenhouse.io/v1/boards/nope/jobs").mock(
        return_value=Response(404, json={"error": "not found"})
    )
    assert await GreenhouseConnector().board_exists("nope") is False
