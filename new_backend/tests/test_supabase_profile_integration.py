"""Opt-in real Supabase Auth/Storage test using a disposable account only."""

import os
import secrets
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.adapters.live.supabase_auth import SupabaseIdentityAdapter
from app.adapters.live.supabase_storage import SupabaseAvatarStorage
from app.main import create_app


@pytest.mark.asyncio
async def test_profile_round_trip_with_real_private_supabase_storage():
    url = os.getenv("TEST_SUPABASE_URL")
    public = os.getenv("TEST_SUPABASE_PUBLISHABLE_KEY")
    secret = os.getenv("TEST_SUPABASE_SECRET_KEY")
    if not all((url, public, secret)):
        pytest.skip("Set TEST_SUPABASE_* for a disposable-account Auth/Storage integration test")
    email = f"profile-test-{uuid4().hex}@example.org"
    password = secrets.token_urlsafe(24)
    headers = {"apikey": secret}
    if not secret.startswith("sb_"):
        headers["Authorization"] = f"Bearer {secret}"
    user_id = None
    async with httpx.AsyncClient(timeout=20) as provider:
        created = await provider.post(f"{url}/auth/v1/admin/users", headers=headers, json={"email": email, "password": password, "email_confirm": True})
        assert created.status_code in {200, 201}, "Disposable account could not be created"
        user_id = created.json()["id"]
        storage = SupabaseAvatarStorage(url, secret, provider)
        revisions = []
        try:
            identity = SupabaseIdentityAdapter(url, public, provider)
            session = await identity.sign_in_password(email, password)
            auth = {"Authorization": f"Bearer {session.access_token}"}
            image = BytesIO()
            Image.new("RGB", (320, 200), "navy").save(image, format="PNG")

            def application():
                app = create_app(avatar_storage=storage)
                app.state.identity_provider = identity
                return app

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
                nickname = await client.patch("/api/v1/me", headers=auth, json={"name": "Storage 测试"})
                assert nickname.status_code == 200, nickname.json()
                uploaded = await client.put("/api/v1/me/avatar", headers={**auth, "Content-Type": "image/png"}, content=image.getvalue())
                assert uploaded.status_code == 200
                revisions.append(uploaded.json()["avatar_revision"])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
                me = (await client.get("/api/v1/me", headers=auth)).json()
                assert me["name"] == "Storage 测试" and me["avatar_revision"] == revisions[0]
                avatar = await client.get("/api/v1/me/avatar", headers=auth)
                assert avatar.status_code == 200
                assert Image.open(BytesIO(avatar.content)).size == (256, 256)
                private = await provider.get(f"{url}/storage/v1/object/public/pskit-avatars/{user_id}/{revisions[0]}.webp", headers={"apikey": public})
                assert private.status_code != 200
                assert (await client.delete("/api/v1/me/avatar", headers=auth)).status_code == 200
                assert (await client.get("/api/v1/me/avatar", headers=auth)).status_code == 404
        finally:
            for revision in revisions:
                await storage.delete(f"{user_id}/{revision}.webp")
            removed = await provider.delete(f"{url}/auth/v1/admin/users/{user_id}", headers=headers)
            assert removed.status_code in {200, 204}, "Disposable account cleanup failed"
