"""
Тести автентифікації HTTP API.

Дефект, який вони закривають: /api/v1/* був повністю відкритий — будь-хто з
доступом до порту міг підкинути сесію біржі, записати API-ключі на чужий
telegram_id або прочитати баланси.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.security import require_api_key
from config import settings


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected():
        return {"ok": True}

    return app


class TestApiKeyAuth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_make_app(), raise_server_exceptions=False)

    def test_rejects_request_without_key(self):
        with patch.object(settings, "api_key", "s3cret"):
            resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 401)

    def test_rejects_wrong_key(self):
        with patch.object(settings, "api_key", "s3cret"):
            resp = self.client.get("/protected", headers={"X-API-Key": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_accepts_header_key(self):
        with patch.object(settings, "api_key", "s3cret"):
            resp = self.client.get("/protected", headers={"X-API-Key": "s3cret"})
        self.assertEqual(resp.status_code, 200)

    def test_accepts_query_key_for_bookmarklet(self):
        # Букмарклет не вміє слати кастомні заголовки — для нього лишений
        # запасний ?api_key=.
        with patch.object(settings, "api_key", "s3cret"):
            resp = self.client.get("/protected", params={"api_key": "s3cret"})
        self.assertEqual(resp.status_code, 200)

    def test_open_when_key_not_configured(self):
        # Порожній API_KEY = захист свідомо вимкнено (з WARNING у лозі).
        with patch.object(settings, "api_key", ""):
            resp = self.client.get("/protected")
        self.assertEqual(resp.status_code, 200)

    def test_prefix_of_valid_key_is_rejected(self):
        with patch.object(settings, "api_key", "s3cret"):
            resp = self.client.get("/protected", headers={"X-API-Key": "s3c"})
        self.assertEqual(resp.status_code, 401)


class TestRoutersAreProtected(unittest.TestCase):
    """Роутери мають бути закриті цілком, а не поендпоінтно."""

    def test_dashboard_router_has_auth_dependency(self):
        from api.routers.dashboard import router

        paths = {r.path for r in router.routes}
        self.assertIn("/api/v1/stats", paths)

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(settings, "api_key", "s3cret"):
            self.assertEqual(client.get("/api/v1/stats").status_code, 401)
            self.assertEqual(client.get("/api/v1/logs").status_code, 401)
            self.assertEqual(
                client.post("/api/v1/settings/global", json={"riskMode": "STRICT"}).status_code,
                401,
            )

    def test_session_receive_requires_key(self):
        from api.routers.webhooks import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        payload = {"exchange": "Binance", "user_id": 1, "cookies_str": "a=b"}
        with patch.object(settings, "api_key", "s3cret"):
            self.assertEqual(
                client.post("/api/v1/session/receive", json=payload).status_code, 401
            )

    def test_mono_webhook_stays_open_for_the_bank(self):
        # Monobank не може слати наш X-API-Key: цей ендпоінт має власний
        # per-card секрет у шляху, тому загальний ключ до нього не чіпляємо.
        from api.routers.webhooks import router

        mono = next(
            r for r in router.routes if "/webhooks/mono/card/" in getattr(r, "path", "")
        )
        self.assertEqual(mono.dependencies, [])


if __name__ == "__main__":
    unittest.main()
