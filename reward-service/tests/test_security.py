"""Xác thực RS256/JWKS OFFLINE + phân quyền cục bộ từ claim `perms` + denylist (Phase 4).

Mọi token ở đây do FAKE Identity ký bằng khoá RSA thật (không có secret đối xứng chung nào).
"""

from __future__ import annotations

import base64
import datetime
import json
import sys
import time
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from settings import RewardConfigError, load_settings  # noqa: E402
from store.repositories import projection as proj  # noqa: E402
from tests.conftest import insert_account, sign_token  # noqa: E402
from tests.harness.fake_identity import AUDIENCE, ISSUER, _SESSION_KEY, sign  # noqa: E402

USER_ROUTE = "/v1/reward/credentials"        # cần reward:access
ADMIN_ROUTE = "/v1/reward/admin/role-audit"  # cần reward:admin


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _claims(identity, **over) -> dict:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": [AUDIENCE], "sub": "a" * 32, "username": "u", "role": "viewer",
            "perms": ["reward:access"], "client_type": "web", "sid": "s1", "jti": "j", "iat": now, "nbf": now,
            "exp": now + 600}
    base.update(over)
    return base


# ── Verify token (401) ───────────────────────────────────────────────────────────

async def test_valid_rs256_token_with_reward_access_is_accepted(client, identity):
    user = await insert_account(None, role="viewer", perms=("reward:access",))
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(user)))).status_code == 200


@pytest.mark.parametrize("over", [{"aud": ["khac"]}, {"aud": "khac"}, {"iss": "http://gia-mao.test"},
                                  {"iss": ISSUER + "/"}], ids=["aud-list", "aud-str", "iss-lạ", "iss-thừa-dấu-/"])
async def test_wrong_audience_or_issuer_is_401(client, identity, over):
    token = sign(_claims(identity, **over), kid=identity.kid)
    resp = await client.get(USER_ROUTE, headers=_hdr(token))
    assert resp.status_code == 401 and resp.json()["detail"] == "Token không hợp lệ"


async def test_expired_token_is_401_with_message(client, identity):
    now = int(time.time())
    token = sign(_claims(identity, exp=now - 300, iat=now - 900, nbf=now - 900), kid=identity.kid)
    resp = await client.get(USER_ROUTE, headers=_hdr(token))
    assert resp.status_code == 401 and resp.json()["detail"] == "Token hết hạn"


@pytest.mark.parametrize("alg", ["HS256", "HS512"])
async def test_symmetric_token_is_rejected_even_if_signed_with_public_material(client, identity, alg):
    """Tấn công nhầm thuật toán: ký HS* bằng chuỗi bất kỳ (kể cả JWKS) → 401. Không có secret chung nào để khớp."""
    forged = jwt.encode(_claims(identity), "bat-ky-secret-nao-do-kiem-thu-123456", algorithm=alg,
                        headers={"kid": identity.kid})
    assert (await client.get(USER_ROUTE, headers=_hdr(forged))).status_code == 401


async def test_alg_none_and_unknown_kid_and_garbage_are_401(client, identity):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_token = f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': identity.kid})}.{b64(_claims(identity))}."
    unknown_kid = jwt.encode(_claims(identity), _SESSION_KEY, algorithm="RS256", headers={"kid": "kid-khong-co"})
    for token in (none_token, unknown_kid, "khong.phai.jwt", "x"):
        assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 401


async def test_missing_or_malformed_authorization_is_401(client):
    assert (await client.get(USER_ROUTE)).status_code == 401
    assert (await client.get(USER_ROUTE, headers={"Authorization": "Basic abc"})).status_code == 401
    assert (await client.get(USER_ROUTE, headers={"Authorization": "Bearer "})).status_code == 401


async def test_old_hs256_secret_is_no_longer_accepted(client, identity, monkeypatch):
    """Dù môi trường còn JWT_SECRET cũ, Reward không đọc nó: token HS256 ký bằng nó vẫn 401."""
    old = "secret-cu-khong-con-duoc-dung-0123456789"
    monkeypatch.setenv("JWT_SECRET", old)
    legacy = jwt.encode({"sub": "x", "username": "x", "role": "admin", "exp": int(time.time()) + 600,
                         "iat": int(time.time())}, old, algorithm="HS256")
    assert (await client.get(ADMIN_ROUTE, headers=_hdr(legacy))).status_code == 401


# ── Phân quyền cục bộ từ perms (403) ─────────────────────────────────────────────

async def test_missing_reward_access_is_403(client, identity):
    viewer = await insert_account(None, role="viewer")
    other_perm = await insert_account(None, role="viewer", perms=("reward:admin",))  # có admin nhưng thiếu access
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(viewer)))).status_code == 403
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(other_perm)))).status_code == 403


async def test_admin_role_counts_as_having_every_reward_permission(client, identity):
    admin = await insert_account(None, role="admin")
    token = sign_token(admin, perms=[])  # claim perms rỗng: quyền đến từ role=admin
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 200
    assert (await client.get(ADMIN_ROUTE, headers=_hdr(token))).status_code == 200


async def test_admin_route_needs_reward_admin_not_just_access_or_dash_admin(client, identity):
    access_only = await insert_account(None, role="viewer", perms=("reward:access",))
    dash = await insert_account(None, role="dash_admin")
    holder = await insert_account(None, role="viewer", perms=("reward:admin",))
    assert (await client.get(ADMIN_ROUTE, headers=_hdr(sign_token(access_only)))).status_code == 403
    assert (await client.get(ADMIN_ROUTE, headers=_hdr(sign_token(dash)))).status_code == 403
    assert (await client.get(ADMIN_ROUTE, headers=_hdr(sign_token(holder)))).status_code == 200


async def test_shim_discord_role_gets_reward_access_from_identity_claims(client, identity):
    """Identity phát `reward:access` cho role=discord (shim) → Reward chỉ đọc claim, không đọc `role`."""
    legacy = await insert_account(None, role="discord")
    token = sign_token(legacy)
    assert "reward:access" in jwt.decode(token, options={"verify_signature": False})["perms"]
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 200
    stripped = sign_token(legacy, perms=[])  # cùng role=discord nhưng claim không có perm → 403
    assert (await client.get(USER_ROUTE, headers=_hdr(stripped))).status_code == 403


# ── Denylist (chặn ngay trước khi token hết hạn) ─────────────────────────────────

@pytest.mark.parametrize("reason,code", [("permission_revoked", 403), ("suspended", 403), ("deleted", 403)])
async def test_blanket_denial_blocks_valid_token_on_read_and_write(client, db_conn, identity, reason, code):
    user = await insert_account(None, role="viewer", perms=("reward:access",))
    token = sign_token(user)
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 200
    await proj.add_denial(db_conn, account_id=user["id"], reason=reason)
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == code
    assert (await client.post("/v1/reward/jobs", headers=_hdr(token),
                              json={"name": "x", "raw_text": "1|1"})).status_code == code
    await proj.lift_denial(db_conn, account_id=user["id"], reason=reason)
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 200


async def test_session_denial_by_sid_and_by_iat(client, db_conn, identity):
    user = await insert_account(None, role="viewer", perms=("reward:access",))
    now = int(time.time())
    same_session = sign_token(user, sid="sid-bi-thu-hoi")
    other_session = sign_token(user, sid="sid-khac")
    await proj.add_denial(db_conn, account_id=user["id"], reason=proj.SESSION_REASON, sid="sid-bi-thu-hoi")
    assert (await client.get(USER_ROUTE, headers=_hdr(same_session))).status_code == 401
    assert (await client.get(USER_ROUTE, headers=_hdr(other_session))).status_code == 200

    # Mọi phiên (sid rỗng): token phát hành TRƯỚC thời điểm thu hồi bị chặn, token SAU đó (đăng nhập lại) thì không.
    when = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    await proj.add_denial(db_conn, account_id=user["id"], reason=proj.SESSION_REASON, sid="", denied_at=when)
    old = sign_token(user, sid="sid-moi", iat=now - 60, nbf=now - 60)
    fresh = sign_token(user, sid="sid-moi2", iat=now + 5, nbf=now - 60)
    assert (await client.get(USER_ROUTE, headers=_hdr(old))).status_code == 401
    assert (await client.get(USER_ROUTE, headers=_hdr(fresh))).status_code == 200


async def test_denial_of_one_account_does_not_affect_another(client, db_conn, identity):
    a = await insert_account(None, role="viewer", perms=("reward:access",))
    b = await insert_account(None, role="viewer", perms=("reward:access",))
    await proj.add_denial(db_conn, account_id=a["id"], reason="suspended")
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(a)))).status_code == 403
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(b)))).status_code == 200


# ── Cấu hình ─────────────────────────────────────────────────────────────────────

def test_settings_have_no_jwt_secret_and_ignore_legacy_env(monkeypatch, identity=None):
    monkeypatch.setenv("JWT_SECRET", "secret-cu-khong-con-duoc-dung-0123456789")
    monkeypatch.setenv("ADMIN_API_BASE", "http://127.0.0.1:8421")
    s = load_settings()
    assert not hasattr(s, "jwt_secret") and not hasattr(s, "admin_api_base")
    assert "secret-cu" not in repr(s)


def test_identity_url_is_required_and_defaults_derive_from_it(monkeypatch):
    monkeypatch.delenv("REWARD_IDENTITY_URL", raising=False)
    with pytest.raises(RewardConfigError, match="REWARD_IDENTITY_URL"):
        load_settings()
    monkeypatch.setenv("REWARD_IDENTITY_URL", "http://127.0.0.1:8430/")
    monkeypatch.delenv("REWARD_ISSUER", raising=False)
    monkeypatch.delenv("REWARD_AUDIENCE", raising=False)
    monkeypatch.delenv("REWARD_IDENTITY_JWKS_URL", raising=False)
    s = load_settings()
    assert s.identity_url == "http://127.0.0.1:8430"
    assert s.identity_jwks_url == "http://127.0.0.1:8430/.well-known/jwks.json"
    assert s.issuer == "http://127.0.0.1:8430" and s.audience == "manager-api"
    assert s.feed_enabled is False


def test_feed_requires_key_and_url_validated(monkeypatch):
    monkeypatch.setenv("REWARD_IDENTITY_URL", "http://127.0.0.1:8430")
    monkeypatch.setenv("REWARD_IDENTITY_FEED_KEY", "khoa-feed-giu-bi-mat-0123456789")
    s = load_settings()
    assert s.feed_enabled and s.identity_feed_url == "http://127.0.0.1:8430"
    monkeypatch.setenv("REWARD_IDENTITY_FEED_URL", "khong-phai-url")
    with pytest.raises(RewardConfigError):
        load_settings()
    monkeypatch.setenv("REWARD_IDENTITY_URL", "ftp://x")
    with pytest.raises(RewardConfigError):
        load_settings()


def test_legacy_database_url_is_never_used_as_fallback(monkeypatch):
    """`DATABASE_URL` cũ từng trỏ vào DB dùng chung `ufsync`; đọc nó sẽ âm thầm giữ coupling ⇒ thiếu REWARD_DATABASE_URL là lỗi."""
    monkeypatch.delenv("REWARD_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@127.0.0.1:5432/ufsync")
    with pytest.raises(RewardConfigError, match="REWARD_DATABASE_URL"):
        load_settings()


def test_extract_token_helpers():
    from security import extract_token, try_extract_token
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        extract_token("")
    assert exc_info.value.status_code == 401 and exc_info.value.detail == "Thiếu token"
    assert try_extract_token("bearer abc") == "abc" and try_extract_token("Basic abc") is None
