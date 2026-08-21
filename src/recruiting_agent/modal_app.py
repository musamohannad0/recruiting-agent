"""Optional Modal deployment scaffold. This module does not deploy on import."""

from __future__ import annotations

try:
    import modal
except ImportError:  # Installed only with `uv sync --extra modal`.
    modal = None


if modal is not None:
    app = modal.App("bespoke-recruiting-agent")
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .uv_pip_install(
            "fastapi>=0.115",
            "uvicorn[standard]>=0.32",
            "jinja2>=3.1",
            "python-multipart>=0.0.12",
            "sqlmodel>=0.0.22",
            "psycopg[binary]>=3.2",
            "httpx>=0.28",
            "tenacity>=9.0",
            "pydantic-settings>=2.6",
            "pyyaml>=6.0",
            "markdownify>=0.13",
            "apscheduler>=3.10,<4",
            "claude-agent-sdk>=0.1.0",
            "langfuse>=3.0",
        )
        .add_local_python_source("recruiting_agent")
    )
    candidate_volume = modal.Volume.from_name("recruiting-agent-workspace", create_if_missing=True)
    deployment_secrets = modal.Secret.from_name("recruiting-agent-secrets")
    common = {
        "image": image,
        "volumes": {"/candidate-workspace": candidate_volume},
        "secrets": [deployment_secrets],
        "env": {"WORKSPACE_DIR": "/candidate-workspace", "DATA_DIR": "/tmp/recruiting-agent-data"},
    }

    @app.function(max_containers=1, **common)
    @modal.asgi_app()
    def web():
        from recruiting_agent.web.app import app as fastapi_app

        return fastapi_app

    @app.function(
        schedule=modal.Cron("*/30 * * * *"),
        timeout=60 * 60,
        retries=modal.Retries(max_retries=3, backoff_coefficient=2.0),
        max_containers=1,
        **common,
    )
    async def coordinator_tick():
        from recruiting_agent.coordinator import SearchCoordinator

        result = await SearchCoordinator().run_cycle("modal_cron")
        candidate_volume.commit()
        return result
else:
    app = None
