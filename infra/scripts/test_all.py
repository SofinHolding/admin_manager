#!/usr/bin/env python3
"""Chạy MỌI bộ test, mỗi bộ trong tiến trình pytest riêng (giống CI từng service chạy độc lập).

  python infra/scripts/test_all.py              # arch + contract + packages + từng service + integration
  python infra/scripts/test_all.py --fast       # bỏ integration (test tiến trình thật, ~25s)
  python infra/scripts/test_all.py --legacy     # thêm test của admin-server legacy
  python infra/scripts/test_all.py --only arch  # chỉ bộ có tên chứa 'arch'

Cần: pip install -r tests/arch/requirements.txt
Thoát 0 khi tất cả xanh. SKIP không tính là đã kiểm chứng: bảng tổng kết hiển thị số SKIP riêng.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "infra" / "service-registry.json").read_text(encoding="utf-8"))


def suites(fast: bool, legacy: bool) -> list[tuple[str, Path]]:
    out = [("arch", ROOT / "tests" / "arch"),
           ("contract", ROOT / "tests" / "contract"),
           ("pkg:contracts", ROOT / "packages" / "contracts" / "python"),
           ("pkg:observability", ROOT / "packages" / "observability"),
           ("pkg:sdk-python", ROOT / "packages" / "sdk-python")]
    for name, svc in REGISTRY["services"].items():
        if svc["status"] == "scaffold":
            out.append((f"svc:{name}", ROOT / svc["path"]))
    if not fast:
        out.append(("integration", ROOT / "tests" / "integration"))
    if legacy:
        out.append(("legacy:admin-server", ROOT / "admin-server"))
    return out


def run(label: str, cwd: Path) -> dict:
    started = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    text = proc.stdout + proc.stderr
    counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|skipped|error|errors)", text)}
    return {"label": label, "code": proc.returncode, "seconds": time.time() - started,
            "passed": counts.get("passed", 0), "failed": counts.get("failed", 0) + counts.get("error", 0)
            + counts.get("errors", 0), "skipped": counts.get("skipped", 0), "output": text}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "infra" / "scripts"))
    import bootstrap_dev
    if bootstrap_dev.ENV_FILE.exists():
        bootstrap_dev.load_into_environ()  # secret DEV ngẫu nhiên của máy này (không nằm trong repo)
    else:
        print("CHÚ Ý: chưa có .env.dev — chạy `python infra/scripts/bootstrap_dev.py` để bật test cần Postgres/Redis.")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--only", help="chỉ chạy bộ có tên chứa chuỗi này")
    args = ap.parse_args()

    results = []
    for label, cwd in suites(args.fast, args.legacy):
        if args.only and args.only not in label:
            continue
        print(f"… {label}", flush=True)
        r = run(label, cwd)
        results.append(r)
        if r["code"] != 0:
            print(r["output"][-2500:])
            if "ModuleNotFoundError" in r["output"]:
                print("  → thiếu thư viện: pip install -r tests/arch/requirements.txt")

    print("\n{:<22} {:>7} {:>7} {:>7} {:>8}  {}".format("bộ test", "pass", "fail", "skip", "giây", ""))
    for r in results:
        status = "OK" if r["code"] == 0 else "LỖI"
        print("{:<22} {:>7} {:>7} {:>7} {:>8.1f}  {}".format(
            r["label"], r["passed"], r["failed"], r["skipped"], r["seconds"], status))
    total_skip = sum(r["skipped"] for r in results)
    bad = [r["label"] for r in results if r["code"] != 0]
    if total_skip:
        print(f"\nCHÚ Ý: {total_skip} test bị SKIP (thiếu hạ tầng) — chưa được kiểm chứng.")
    if not results:
        print("Không có bộ test nào được chọn.")
        return 2
    print("\nKẾT QUẢ:", "TẤT CẢ XANH" if not bad else f"LỖI ở: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
