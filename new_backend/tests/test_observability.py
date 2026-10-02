import logging

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_request_metrics_are_admin_only_and_logs_hide_credentials(caplog):
    app = create_app(Settings(admin_api_key="admin-secret"))
    with caplog.at_level(logging.INFO, logger="pskit.requests"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            response = await client.get("/health/live?token=private-query", headers={
                "Authorization": "Bearer private-auth",
            })
            hidden = await client.get("/internal/metrics")
            wrong = await client.get("/internal/metrics", headers={"X-Admin-Key": "bad"})
            metrics = await client.get("/internal/metrics", headers={
                "X-Admin-Key": "admin-secret",
            })

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert hidden.status_code == wrong.status_code == 404
    assert metrics.status_code == 200
    assert {"method": "GET", "route": "/health/live", "status": 200, "count": 1} in [
        {key: row[key] for key in ("method", "route", "status", "count")}
        for row in metrics.json()["requests"]
    ]
    assert "private-auth" not in caplog.text
    assert "private-query" not in caplog.text
    assert "admin-secret" not in caplog.text
