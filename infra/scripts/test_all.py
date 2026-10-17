#!/usr/bin/env python3
"""Cổng chất lượng DUY NHẤT: dựng hạ tầng DEV riêng, migrate, rồi chạy mọi bộ test (mỗi bộ một tiến trình riêng).

  python infra/scripts/test_all.py                # tất cả: arch, contract, packages, từng service, integration, e2e, frontend
  python infra/scripts/test_all.py --fast         # bỏ test tiến trình thật + e2e + frontend build
  python infra/scripts/test_all.py --legacy       # thêm test của admin-server legacy
  python infra/scripts/test_all.py --only e2e     # chỉ bộ có tên chứa chuỗi này
  python infra/scripts/test_all.py --no-bootstrap # không đụng Docker/pip (đã có sẵn hạ tầng)

Các bước chuẩn bị (có thể bỏ bằng --no-bootstrap):
  1. `.env.dev` (secret DEV ngẫu nhiên) qua bootstrap_dev.py;  2. thư viện Python còn thiếu (pip, chỉ khi thiếu);
  3. Postgres + Redis DEV RIÊNG (`docker compose -f infra/docker/compose.dev.yml up -d`, project `manager-platform-dev`);
  4. migration từng service (job riêng).
Thoát 0 CHỈ khi mọi bước và mọi bộ test xanh. SKIP không phải PASS: bảng tổng kết liệt kê SKIP riêng và coi
SKIP vì thiếu hạ tầng là LỖI khi chạy bản đầy đủ (không bootstrap được hạ tầng thì không được báo xanh).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra" / "scripts"))
REGISTRY = json.loads((ROOT / "infra" / "service-registry.json").read_text(encoding="utf-8"))
COMPOSE = ROOT / "infra" / "docker" / "compose.dev.yml"
MIGRATING = ("identity-service", "integration-service", "analytics-service")
PY_DEPS = {"pytest": "pytest>=8", "yaml": "pyyaml>=6", "jsonschema": "jsonschema>=4.18", "pytest_asyncio": "pytest-asyncio",
           "fastapi": "fastapi>=0.115", "uvicorn": "uvicorn[standard]>=0.30", "asyncpg": "asyncpg>=0.29",
           "httpx": "httpx>=0.27", "jwt": "pyjwt[crypto]>=2.15.0", "cryptography": "cryptography>=42", "bcrypt": "bcrypt>=4.1",
           "redis": "redis>=5.0", "email_validator": "pydantic[email]>=2.7", "tzdata": "tzdata>=2024.1"}


def log(msg: str) -> None:
    print(msg, flush=True)


# ── Chuẩn bị ─────────────────────────────────────────────────────────────────────────────────────────────────────

def step_env() -> bool:
    import bootstrap_dev
    if not bootstrap_dev.ENV_FILE.exists():
        log("… bootstrap_dev (sinh .env.dev ngẫu nhiên)")
        r = subprocess.run([sys.executable, str(ROOT / "infra" / "scripts" / "bootstrap_dev.py")], cwd=ROOT)
        if r.returncode != 0:
            return False
    bootstrap_dev.load_into_environ()
    return True


def step_python_deps() -> bool:
    missing = [spec for mod, spec in PY_DEPS.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return True
    log(f"… pip install (thiếu: {', '.join(missing)})")
    return subprocess.run([sys.executable, "-m", "pip", "install", "-q", *missing]).returncode == 0


def step_infra() -> bool:
    import devenv
    if devenv.pg_ready() and devenv.redis_ready():
        return True
    if not shutil.which("docker"):
        log("LỖI: không có docker và hạ tầng DEV chưa chạy.")
        return False
    log("… docker compose up (stack DEV riêng manager-platform-dev: Postgres 55440, Redis 26390)")
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--wait"], cwd=ROOT)
    if r.returncode != 0:
        return False
    deadline = time.time() + 60
    while time.time() < deadline:
        if devenv.pg_ready() and devenv.redis_ready():
            return True
        time.sleep(1)
    log("LỖI: Postgres/Redis DEV không sẵn sàng sau 60s.")
    return False


def step_migrate() -> bool:
    from dev_stack import DevStack
    stack = DevStack()
    for name in ("identity", "integration", "analytics"):
        log(f"… migrate {name}")
        try:
            stack.migrate(name)
        except RuntimeError as exc:
            log(f"LỖI: {exc}")
            return False
    return True


# ── Bộ test ──────────────────────────────────────────────────────────────────────────────────────────────────────

def pytest_suites(fast: bool, legacy: bool) -> list[tuple[str, Path]]:
    out = [("arch", ROOT / "tests" / "arch"),
           ("contract", ROOT / "tests" / "contract"),
           ("pkg:contracts", ROOT / "packages" / "contracts" / "python"),
           ("pkg:observability", ROOT / "packages" / "observability"),
           ("pkg:sdk-python", ROOT / "packages" / "sdk-python"),
           ("app:collector", ROOT / "apps" / "local-fallback-tool")]
    for name, svc in REGISTRY["services"].items():
        if (ROOT / svc["path"]).is_dir() and ((ROOT / svc["path"] / "tests").is_dir() or name == "reward-service"):
            out.append((f"svc:{name}", ROOT / svc["path"]))
    if not fast:
        out.append(("integration", ROOT / "tests" / "integration"))
        out.append(("e2e", ROOT / "tests" / "e2e"))
    if legacy:
        out.append(("legacy:admin-server", ROOT / "legacy" / "admin-server"))
    return out


def run_pytest(label: str, cwd: Path) -> dict:
    started = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stdout + proc.stderr
    counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|skipped|error|errors)", text)}
    return {"label": label, "code": proc.returncode, "seconds": time.time() - started,
            "passed": counts.get("passed", 0), "failed": counts.get("failed", 0) + counts.get("error", 0)
            + counts.get("errors", 0), "skipped": counts.get("skipped", 0), "output": text,
            "external": re.findall(r"SKIPPED \[\d+\] (.*NOT_RUN_EXTERNAL_DEPENDENCY.*)", text)}


def run_cmd(label: str, cmd: list[str], cwd: Path) -> dict:
    started = time.time()
    npm = shutil.which(cmd[0])
    if not npm:
        return {"label": label, "code": 127, "seconds": 0.0, "passed": 0, "failed": 1, "skipped": 0,
                "output": f"không tìm thấy lệnh `{cmd[0]}` (cần Node.js để kiểm frontend)"}
    proc = subprocess.run([npm, *cmd[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stdout + proc.stderr
    m = re.search(r"Tests\s+(?:\x1b\[[0-9;]*m)*\s*(\d+) passed", text)
    return {"label": label, "code": proc.returncode, "seconds": time.time() - started,
            "passed": int(m.group(1)) if m else (1 if proc.returncode == 0 else 0),
            "failed": 0 if proc.returncode == 0 else 1, "skipped": 0, "output": text}


def frontend_suites() -> list[tuple[str, tuple[list[str], Path]]]:
    """Mỗi bước = (nhãn, (lệnh, thư mục chạy)). Hai app có kiểm kiểu: manager-web (đủ bộ) và local-fallback-tool (typecheck)."""
    steps: list[tuple[str, tuple[list[str], Path]]] = []
    web = ROOT / "apps" / "manager-web"
    if (web / "package.json").is_file():
        if not (web / "node_modules").is_dir():
            steps.append(("web:install", (["npm", "ci", "--no-audit", "--no-fund"], web)))
        steps += [("web:typecheck", (["npm", "run", "typecheck"], web)), ("web:test", (["npm", "test"], web)),
                  ("web:build", (["npm", "run", "build"], web))]
    tool = ROOT / "apps" / "local-fallback-tool"
    if (tool / "package.json").is_file():
        if not (tool / "node_modules").is_dir():   # không tải binary Electron: typecheck không cần
            steps.append(("collector-ui:install", (["npm", "ci", "--no-audit", "--no-fund"], tool)))
        steps.append(("collector-ui:typecheck", (["npm", "run", "typecheck"], tool)))
    return steps


# ── Điều phối ────────────────────────────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--no-bootstrap", action="store_true")
    ap.add_argument("--only", help="chỉ chạy bộ có tên chứa chuỗi này")
    args = ap.parse_args()

    if not args.no_bootstrap:
        for name, fn in (("môi trường DEV", step_env), ("thư viện Python", step_python_deps), ("hạ tầng DEV", step_infra),
                         ("migration", step_migrate)):
            if not fn():
                print(f"\nKẾT QUẢ: LỖI ở bước chuẩn bị `{name}` — không chạy test (không báo xanh khi hạ tầng chưa dựng được).")
                return 1
    else:
        import bootstrap_dev
        if bootstrap_dev.ENV_FILE.exists():
            bootstrap_dev.load_into_environ()

    results = []
    todo: list[tuple[str, object]] = [(label, cwd) for label, cwd in pytest_suites(args.fast, args.legacy)]
    if not args.fast:
        todo += [(label, cmd) for label, cmd in frontend_suites()]
    for label, target in todo:
        if args.only and args.only not in label:
            continue
        log(f"… {label}")
        r = run_pytest(label, target) if isinstance(target, Path) else run_cmd(label, target[0], target[1])
        results.append(r)
        if r["code"] != 0:
            print(r["output"][-2500:])

    print("\n{:<24} {:>7} {:>7} {:>7} {:>8}  {}".format("bộ test", "pass", "fail", "skip", "giây", ""))
    for r in results:
        print("{:<24} {:>7} {:>7} {:>7} {:>8.1f}  {}".format(
            r["label"], r["passed"], r["failed"], r["skipped"], r["seconds"], "OK" if r["code"] == 0 else "LỖI"))
    external = [e for r in results for e in r.get("external", [])]
    total_skip = sum(r["skipped"] for r in results)
    bad = [r["label"] for r in results if r["code"] != 0]
    if external:  # phụ thuộc NGOÀI repo (vd. ảnh nginx) cố ý không kéo về: liệt kê rõ là CHƯA KIỂM CHỨNG, không làm đỏ cổng
        print("\nCHƯA KIỂM CHỨNG (NOT_RUN_EXTERNAL_DEPENDENCY):")
        for e in external:
            print("  -", e)
    unexplained = total_skip - len(external)
    if unexplained > 0:
        print(f"\nCHÚ Ý: {unexplained} test bị SKIP không có lý do phụ thuộc ngoài — chưa được kiểm chứng (xem -rs).")
        if not args.fast and not args.only and not args.no_bootstrap:
            bad.append("skipped-tests")  # bản đầy đủ đã dựng hạ tầng: SKIP nghĩa là còn kẽ hở kiểm chứng
    if not results:
        print("Không có bộ test nào được chọn.")
        return 2
    print("\nKẾT QUẢ:", "TẤT CẢ XANH" if not bad else f"LỖI ở: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
