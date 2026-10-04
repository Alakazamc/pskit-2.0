import httpx
import pytest

from app.adapters.live.supabase_auth import SupabaseIdentityAdapter
from app.main import create_app


def picture() -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (400, 300), "blue").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_avatar_upload_replace_delete_is_authenticated_and_owned():
    from io import BytesIO

    from PIL import Image

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        headers = {"Authorization": f"Bearer {alice['access_token']}", "Content-Type": "image/png"}
        assert (await client.put("/api/v1/me/avatar", content=picture())).status_code == 401
        uploaded = await client.put("/api/v1/me/avatar", headers=headers, content=picture())
        assert uploaded.status_code == 200
        revision = uploaded.json()["avatar_revision"]
        assert revision
        image = await client.get("/api/v1/me/avatar", headers=headers)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/webp"
        assert Image.open(BytesIO(image.content)).size == (256, 256)
        assert (await client.get("/api/v1/me/avatar", headers={"Authorization": f"Bearer {bob['access_token']}"})).status_code == 404
        invalid = await client.put("/api/v1/me/avatar", headers=headers, content=b"not an image")
        assert invalid.status_code == 422
        oversize = await client.put("/api/v1/me/avatar", headers=headers, content=b"x" * (4 * 1024 * 1024 + 1))
        assert oversize.status_code == 413
        svg = await client.put("/api/v1/me/avatar", headers={**headers, "Content-Type": "image/svg+xml"}, content=b"<svg/>")
        assert svg.status_code == 415
        assert (await client.get("/api/v1/me", headers=headers)).json()["avatar_revision"] == revision
        replaced = await client.put("/api/v1/me/avatar", headers=headers, content=picture())
        assert replaced.json()["avatar_revision"] != revision
        removed = await client.delete("/api/v1/me/avatar", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["avatar_revision"] is None
        assert (await client.get("/api/v1/me/avatar", headers=headers)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_status", [404, 400])
async def test_avatar_uses_private_supabase_storage_and_survives_app_restart(missing_status):
    from app.adapters.live.supabase_storage import SupabaseAvatarStorage

    user = {"id": "alice", "email": "alice@example.org", "is_anonymous": False, "user_metadata": {}}
    objects = {}
    bucket = None

    def provider(request):
        nonlocal bucket
        import json
        path = request.url.path
        if path == "/auth/v1/user":
            assert request.headers["authorization"] == "Bearer provider-token"
            if request.method == "PUT":
                user["user_metadata"].update(json.loads(request.content)["data"])
            return httpx.Response(200, json=user)
        assert request.headers["apikey"] == "server-secret"
        if path == "/storage/v1/bucket/pskit-avatars":
            return httpx.Response(200, json=bucket) if bucket else httpx.Response(missing_status, json={"statusCode": "404", "code": "NoSuchBucket"})
        if path == "/storage/v1/bucket":
            bucket = json.loads(request.content)
            assert bucket["public"] is False
            return httpx.Response(200, json={"name": bucket["name"]})
        prefix = "/storage/v1/object/"
        if path == prefix + "pskit-avatars" and request.method == "DELETE":
            for key in json.loads(request.content)["prefixes"]:
                objects.pop(key, None)
            return httpx.Response(200, json=[])
        key = path.removeprefix(prefix).removeprefix("authenticated/").removeprefix("pskit-avatars/")
        assert key.startswith("alice/")
        if request.method == "POST":
            assert request.headers["content-type"] == "image/webp"
            objects[key] = request.content
            return httpx.Response(200, json={"Key": key})
        return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
        def application():
            app = create_app(avatar_storage=SupabaseAvatarStorage("https://supabase.test", "server-secret", transport))
            app.state.identity_provider = SupabaseIdentityAdapter("https://supabase.test", "public", transport)
            return app

        headers = {"Authorization": "Bearer provider-token", "Content-Type": "image/png"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
            uploaded = await client.put("/api/v1/me/avatar", headers=headers, content=picture())
            assert uploaded.status_code == 200
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
            restored = await client.get("/api/v1/me/avatar", headers=headers)
            assert restored.status_code == 200
            assert (await client.delete("/api/v1/me/avatar", headers=headers)).status_code == 200
        assert objects == {}


@pytest.mark.asyncio
async def test_nickname_is_trimmed_and_restored_for_all_sessions_without_changing_identity():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        second = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        updated = await client.patch("/api/v1/me", headers={"Authorization": f"Bearer {first['access_token']}"}, json={"name": "  小林  "})
        assert updated.status_code == 200
        assert updated.json()["name"] == "小林"
        assert updated.json()["id"] == first["user"]["id"]
        restored = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {second['access_token']}"})
        assert restored.json()["name"] == "小林"
        other = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {bob['access_token']}"})
        assert other.json()["name"] == "bob"
        assert (await client.patch("/api/v1/me", json={"name": "Name"})).status_code == 401
        for name in ("  ", "x" * 81, "a\nb"):
            response = await client.patch("/api/v1/me", headers={"Authorization": f"Bearer {first['access_token']}"}, json={"name": name})
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_nickname_uses_supabase_user_metadata_and_preserves_unrelated_metadata():
    user = {"id": "alice", "email": "alice@example.org", "is_anonymous": False,
            "user_metadata": {"full_name": "Alice", "other": "keep"}}

    def provider(request):
        assert request.headers["authorization"] == "Bearer provider-token"
        if request.method == "PUT":
            import json
            assert json.loads(request.content) == {"data": {"full_name": "小林"}}
            user["user_metadata"].update(json.loads(request.content)["data"])
        return httpx.Response(200, json=user)

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
        app.state.identity_provider = SupabaseIdentityAdapter("https://supabase.test", "public", transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            updated = await client.patch("/api/v1/me", headers={"Authorization": "Bearer provider-token"}, json={"name": "小林"})
            assert updated.status_code == 200
            assert updated.json()["name"] == "小林"
            restored = await client.get("/api/v1/me", headers={"Authorization": "Bearer provider-token"})
            assert restored.json()["name"] == "小林"
            assert user["user_metadata"]["other"] == "keep"
