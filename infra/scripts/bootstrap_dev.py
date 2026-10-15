#!/usr/bin/env python3
"""Bootstrap môi trường DEV: sinh secret NGẪU NHIÊN, dựng file cấu hình hạ tầng từ template.

    python infra/scripts/bootstrap_dev.py            # tạo .env.dev nếu chưa có (KHÔNG ghi đè secret đã có)
    python infra/scripts/bootstrap_dev.py --rotate   # sinh lại TOÀN BỘ secret DEV (cần dựng lại volume DEV)
    python infra/scripts/bootstrap_dev.py --check    # chỉ kiểm tra .env.dev đầy đủ và đủ mạnh

Quy tắc bảo mật (docs/operations/dev-agent-full-implementation-prompt.md mục 6):
  * Không secret nào được commit: `.env.dev` và thư mục `.dev/` nằm trong .gitignore.
  * Không có mật khẩu mặc định cố định kiểu `dev-secret-thay-doi-khi-deploy`; mỗi máy dev có secret riêng.
  * Không dùng secret production trong DEV (script không bao giờ đọc biến môi trường production).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env.dev"
GEN_DIR = ROOT / ".dev"
TEMPLATES = ROOT / "infra" / "docker" / "templates"

SERVICES = ("identity", "integration", "analytics", "reward")
FEED_CONSUMERS = {  # <nguồn phát feed>: <các bên đọc>
    "IDENTITY": ("INTEGRATION", "ANALYTICS", "REWARD"),
    "INTEGRATION": ("ANALYTICS",),
}

# Giá trị KHÔNG phải secret (cổng, tên) — cố định để các script và test dùng chung.
CONSTANTS = {
    "DEV_PG_HOST": "127.0.0.1",
    "DEV_PG_PORT": "55600",
    "DEV_REDIS_HOST": "127.0.0.1",
    "DEV_REDIS_PORT": "26390",
    "DEV_ISSUER": "http://127.0.0.1:8430",
    "DEV_AUDIENCE": "manager-api",
}


def _tok(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def _fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def generate() -> dict[str, str]:
    values = dict(CONSTANTS)
    values["DEV_PG_ADMIN_PASSWORD"] = _tok()
    for svc in SERVICES:
        values[f"DEV_PG_{svc.upper()}_PASSWORD"] = _tok()
    values["DEV_REDIS_ANALYTICS_PASSWORD"] = _tok()
    values["IDENTITY_KEY_ENCRYPTION_KEY"] = _fernet_key()   # mã hoá khoá ký private trong DB
    values["REWARD_TOKEN_KEY"] = _fernet_key()              # mã hoá token Discord của Reward
    values["INTEGRATION_REVEAL_KEY"] = _fernet_key()        # mã hoá reveal credential cấp hộ của Integration
    for source, readers in FEED_CONSUMERS.items():
        for reader in readers:
            values[f"{source}_FEED_KEY_{reader}"] = _tok(32)  # khoá đọc feed nội bộ, mỗi bên một khoá
    values["GATEWAY_INTERNAL_PROBE_KEY"] = _tok()
    return values


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def write_env(values: dict[str, str], path: Path = ENV_FILE) -> None:
    header = ("# SINH TỰ ĐỘNG bởi infra/scripts/bootstrap_dev.py — CHỈ DÙNG CHO DEV, KHÔNG COMMIT.\n"
              "# Secret ngẫu nhiên riêng của máy này. Sinh lại: python infra/scripts/bootstrap_dev.py --rotate\n")
    body = "\n".join(f"{k}={v}" for k, v in sorted(values.items())) + "\n"
    path.write_text(header + body, encoding="utf-8", newline="\n")
    try:
        os.chmod(path, 0o600)  # trên Windows chmod gần như không có tác dụng; vẫn gọi cho Linux/macOS
    except OSError:
        pass


def missing_or_weak(values: dict[str, str]) -> list[str]:
    problems = []
    for key in generate():
        v = values.get(key, "")
        if not v:
            problems.append(f"thiếu {key}")
        elif key not in CONSTANTS and len(v) < 24:
            problems.append(f"{key} quá ngắn")
    for key, v in values.items():
        if key not in CONSTANTS and ("change_me" in v or v.startswith("dev-")):
            problems.append(f"{key} là giá trị mặc định không an toàn")
    return problems


def render_templates(values: dict[str, str]) -> list[Path]:
    """Dựng file hạ tầng từ template vào `.dev/` (gitignored). Template dùng ${TÊN}."""
    written = []
    for tpl in sorted(TEMPLATES.rglob("*.tpl")):
        rel = tpl.relative_to(TEMPLATES).with_suffix("")
        out = GEN_DIR / rel
        text = tpl.read_text(encoding="utf-8")
        for key, val in values.items():
            text = text.replace("${" + key + "}", val)
        if "${" in text:
            unresolved = sorted({t.split("}")[0] for t in text.split("${")[1:]})
            raise SystemExit(f"Template {tpl.name} còn biến chưa có giá trị: {unresolved}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        written.append(out)
    return written


def ensure(rotate: bool = False) -> dict[str, str]:
    values = read_env()
    fresh = generate()
    if rotate or not values:
        values = fresh
    else:  # bổ sung khoá còn thiếu, giữ nguyên secret đã có
        for k, v in fresh.items():
            values.setdefault(k, v)
    write_env(values)
    render_templates(values)
    return values


def load_into_environ(path: Path = ENV_FILE) -> dict[str, str]:
    """Nạp .env.dev vào os.environ (không ghi đè biến đã có). Dùng cho test và script."""
    values = read_env(path)
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rotate", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        problems = missing_or_weak(read_env())
        for p in problems:
            print("!", p)
        print("OK" if not problems else "KHÔNG ĐẠT")
        return 1 if problems else 0
    existed = ENV_FILE.exists()
    values = ensure(rotate=args.rotate)
    print(f"{'Đã sinh lại' if args.rotate else ('Giữ nguyên' if existed else 'Đã tạo')} {ENV_FILE.name} "
          f"({len(values)} giá trị, secret không in ra). File hạ tầng dựng trong .dev/")
    if args.rotate:
        print("LƯU Ý: secret DB/Redis đổi nên phải dựng lại volume DEV: docker compose -f "
              "infra/docker/compose.dev.yml down -v && up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
