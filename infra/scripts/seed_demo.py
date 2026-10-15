"""Seed dữ liệu DEMO vào stack đang chạy (compose `infra/compose/stack.yml` hoặc `dev_stack.py`) để thử tay trên web.

  python infra/scripts/seed_demo.py                      # gateway mặc định http://127.0.0.1:8400
  python infra/scripts/seed_demo.py --gateway http://127.0.0.1:8400 --identity-logs "docker logs manager-platform-stack-identity-1"

Làm: tạo tài khoản admin + viewer (mật khẩu NGẪU NHIÊN, ghi vào `.dev/demo-accounts.json` — gitignored, KHÔNG in ra màn hình),
đăng ký platform demo (dữ liệu Registry), cấp credential cho một Tool mẫu rồi đẩy snapshot qua `IntegrationClient`
(kênh + bài đăng + sự kiện trong 14 ngày gần nhất). Chạy lại nhiều lần an toàn (bỏ qua phần đã có).
Chỉ dùng cho DEV: seed invite key trực tiếp vào identity_db DEV bằng DSN của chính user identity_svc.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import random
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra" / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "sdk-python"))
import bootstrap_dev  # noqa: E402
import devenv  # noqa: E402

ACCOUNTS_FILE = ROOT / ".dev" / "demo-accounts.json"
PLATFORM = "demo_social"
CHANNELS = [("demo-ch-1", "Kênh Demo Một", "BR", "Brazil"), ("demo-ch-2", "Kênh Demo Hai", "VN", "Vietnam"),
            ("demo-ch-3", "Kênh Demo Ba", "US", "United States")]


def load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    return {}


def save_accounts(data: dict) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def seed_invite_key(role: str) -> str:
    import asyncpg

    key = "demo-" + secrets.token_hex(6)

    async def run() -> None:
        conn = await asyncpg.connect(devenv.pg_dsn("identity"))
        try:
            await conn.execute("INSERT INTO invite_keys (key, role, label, max_uses, expires_at) "
                               "VALUES ($1,$2,'demo',5, now()+interval '7 days')", key, role)
        finally:
            await conn.close()
    asyncio.run(run())
    return key


def verify_email_from_logs(http: httpx.Client, log_cmd: str, email: str) -> None:
    pattern = re.compile(re.escape(email) + r": (\S+?)[\"\s}]")
    deadline = time.time() + 20
    while time.time() < deadline:
        out = subprocess.run(log_cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        found = pattern.findall(out.stdout + out.stderr)
        if found:
            r = http.get("/v1/auth/verify-email", params={"token": found[-1]})
            r.raise_for_status()
            return
        time.sleep(1)
    raise SystemExit(f"Không thấy token xác thực email của {email} trong log identity (IDENTITY_ENV phải là dev).")


def ensure_user(http: httpx.Client, accounts: dict, name: str, role: str, log_cmd: str, *,
                admin_headers: dict | None = None, preset: str | None = None) -> dict:
    if name in accounts:
        r = http.post("/v1/auth/login", json={"username": name, "password": accounts[name]["password"]})
        if r.status_code == 200:
            return accounts[name]
    password = "Demo-" + secrets.token_urlsafe(12) + "9a"
    email = f"{name}@demo.example.com"
    if admin_headers is not None:
        kr = http.post("/v1/admin/keys", headers=admin_headers,
                       json={"preset": preset} if preset else {"role": role})
        kr.raise_for_status()
        key = kr.json()["key"]
    else:
        key = seed_invite_key(role)
    body = {"key": key, "username": name, "password": password, "email": email}
    r = http.post("/v1/auth/register", json=body)
    if r.status_code not in (200, 201):
        raise SystemExit(f"Đăng ký {name} lỗi {r.status_code}: {r.text[:200]}")
    verify_email_from_logs(http, log_cmd, email)
    accounts[name] = {"username": name, "password": password, "email": email, "role": role}
    save_accounts(accounts)
    return accounts[name]


def bearer(http: httpx.Client, acc: dict) -> dict:
    r = http.post("/v1/auth/login", json={"username": acc["username"], "password": acc["password"]})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://127.0.0.1:8400")
    ap.add_argument("--identity-logs", default="docker logs manager-platform-stack-identity-1")
    args = ap.parse_args()
    bootstrap_dev.load_into_environ()
    http = httpx.Client(base_url=args.gateway, timeout=20)
    accounts = load_accounts()

    admin = ensure_user(http, accounts, "demoadmin", "admin", args.identity_logs)
    ah = bearer(http, admin)
    r = http.put(f"/v1/integrations/admin/platforms/{PLATFORM}", headers=ah, json={"display_name": "Demo Social"})
    r.raise_for_status()

    owner = ensure_user(http, accounts, "demoowner", "viewer", args.identity_logs,
                        admin_headers=ah, preset="viewer_tool")
    oh = bearer(http, owner)
    deadline = time.time() + 40
    clients: list = []
    while time.time() < deadline and not clients:
        clients = http.get("/v1/integrations/clients", headers=oh).json().get("data", {}).get("items", [])
        time.sleep(1)
    if not clients:
        raise SystemExit("Integration chưa tạo Tool cá nhân cho demoowner (kiểm log integration — capability:tool_integration).")
    cid = clients[0]["client_id"]

    # Khoá Tool cá nhân do hệ thống cấp sẵn: lần đầu chủ `reveal`; chạy lại (đã reveal) thì chủ xoay khoá để lấy plaintext mới.
    api_key = None
    deadline = time.time() + 40
    while time.time() < deadline and not api_key:
        r = http.post("/v1/integrations/me/credential/reveal", headers=oh)
        if r.status_code == 200:
            api_key = r.json()["data"]["api_key"]
        elif r.status_code in (404, 410):
            active = [c for c in http.get(f"/v1/integrations/clients/{cid}", headers=oh).json()["data"]["credentials"]
                      if c["status"] == "active"]
            r = (http.post(f"/v1/integrations/clients/{cid}/credentials/{active[0]['key_id']}/rotate", headers=oh,
                           json={}) if active else
                 http.post(f"/v1/integrations/clients/{cid}/credentials", headers=oh, json={}))
            r.raise_for_status()
            api_key = r.json()["data"]["api_key"]
        else:
            time.sleep(1)  # chờ Integration nhận email_verified qua feed (403 EMAIL_NOT_VERIFIED)
    if not api_key:
        raise SystemExit("Không nhận được khoá Tool (email chưa được Integration ghi nhận).")

    from manager_sdk.integration_client import IntegrationClient

    async def push() -> dict:
        tool = IntegrationClient(args.gateway, api_key, retries=2, timeout=20)
        try:
            await tool.handshake(device_id="demo-device", hostname="demo-host", os_name="windows", app_version="1",
                                 tool_name="Demo Tool", platforms=[PLATFORM], capabilities=["snapshots.push"])
            now = dt.datetime.now(dt.timezone.utc)
            ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            rnd = random.Random(7)
            resources = [{"external_id": c[0], "resource_type": "channel", "display_name": c[1], "state": "active",
                          "country": {"code": c[2], "name": c[3]}, "managed": True, "active": True, "observed_at": ts} for c in CHANNELS]
            content, events = [], []
            for ci, c in enumerate(CHANNELS):
                for d in range(1, 15):
                    day = (now.date() - dt.timedelta(days=d)).isoformat()
                    for k in range(rnd.randint(1, 3)):
                        content.append({"external_id": f"{c[0]}-p-{d}-{k}", "resource_external_id": c[0], "published_date": day,
                                        "metrics": {"views": rnd.randint(200, 9000) * (ci + 1), "reactions": rnd.randint(5, 400),
                                                    "comments": rnd.randint(0, 60)}, "observed_at": ts})
                        events.append({"external_id": f"{c[0]}-e-{d}-{k}", "resource_external_id": c[0], "kind": "post_ok", "occurred_date": day})
            return await tool.push_snapshot(platform=PLATFORM, observed_at=ts, report_timezone="UTC", snapshot_type="full",
                                            scope={"resources": True}, resources=resources, content=content, events=events)
        finally:
            await tool.aclose()

    out = asyncio.run(push())
    print(f"OK: snapshot {out.get('status')} — {out.get('totals')}")
    print(f"Tài khoản demo (mật khẩu nằm trong {ACCOUNTS_FILE.relative_to(ROOT)}): demoadmin (admin), demoowner (viewer, chủ Tool 'Demo Tool')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
