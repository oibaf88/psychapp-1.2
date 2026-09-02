import pytest
from starlette.testclient import TestClient

from app.main import app


def test_cors_allowed_local_origins():
    client = TestClient(app)

    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://192.168.1.100:5173",
        "http://10.0.0.5:3000",
        "http://172.16.0.1:5173",
    ]

    for origin in allowed_origins:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == origin, (
            f"Expected origin {origin} to be allowed by CORS middleware"
        )


def test_cors_disallowed_public_tunnels_and_malicious_origins():
    client = TestClient(app)

    disallowed_origins = [
        "https://evil.trycloudflare.com",
        "https://attacker.ngrok-free.app",
        "https://malicious.ngrok.io",
        "http://192.168.1.100.evil.com",
        "http://localhost.attacker.com",
        "https://evil-site.com",
    ]

    for origin in disallowed_origins:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in response.headers, (
            f"Expected origin {origin} to be rejected by CORS middleware"
        )
