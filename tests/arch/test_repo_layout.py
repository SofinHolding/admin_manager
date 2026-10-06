"""Chuẩn skeleton thư mục (docs/architecture/repo-layout-v1.md §3). Áp dụng cho `services/*` ngay từ bây giờ."""

from __future__ import annotations

from pathlib import Path

from archlib import ROOT, load_registry

SERVICES_DIR = ROOT / "services"
REQUIRED = ["README.md", "Dockerfile", ".env.example", "requirements.txt", "pytest.ini", "tests"]


def skeleton_problems(service_dir: Path, package: str, has_db: bool) -> list[str]:
    out = [f"thiếu {name}" for name in REQUIRED if not (service_dir / name).exists()]
    if not (service_dir / "src" / package / "main.py").is_file():
        out.append(f"thiếu src/{package}/main.py")
    migrations = (service_dir / "migrations").is_dir()
    if has_db and not migrations:
        out.append("service có DB nhưng thiếu migrations/")
    if not has_db and migrations:
        out.append("service không có DB nhưng có migrations/")
    return out


def test_every_service_dir_is_registered_and_follows_the_skeleton():
    reg = load_registry()["services"]
    by_dir = {Path(s["path"]).name: (n, s) for n, s in reg.items() if s["path"].startswith("services/")}
    on_disk = sorted(p.name for p in SERVICES_DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "__")))
    assert [d for d in on_disk if d not in by_dir] == [], "thư mục trong services/ chưa khai ở service-registry.json"
    problems = {}
    for d in on_disk:
        name, svc = by_dir[d]
        found = skeleton_problems(SERVICES_DIR / d, svc["package"], svc.get("database") is not None)
        if found:
            problems[name] = found
    assert problems == {}


def test_env_examples_hold_no_real_secrets_and_use_the_service_prefix():
    reg = load_registry()["services"]
    for d in sorted(p for p in SERVICES_DIR.iterdir() if p.is_dir()):
        env = d / ".env.example"
        if not env.is_file():
            continue
        prefix = d.name.replace("-service", "").replace("api-", "").upper() + "_"
        keys = [ln.split("=", 1)[0].strip() for ln in env.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
        wrong = [k for k in keys if not (k.startswith(prefix) or k.startswith("API_GATEWAY_") or k in {"LOG_LEVEL"})]
        assert wrong == [], f"{d.name}: biến thiếu tiền tố {prefix}: {wrong}"
    assert reg  # registry đọc được


def test_skeleton_checker_detects_violations(tmp_path):
    """Test của test: thư mục thiếu file / sai migrations phải bị bắt."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "migrations").mkdir()
    found = skeleton_problems(tmp_path, "pkg", has_db=False)
    assert "service không có DB nhưng có migrations/" in found and "thiếu Dockerfile" in found and "thiếu src/pkg/main.py" in found
    assert "service có DB nhưng thiếu migrations/" in skeleton_problems(tmp_path / "src", "pkg", has_db=True)


# ── Cấp cao nhất của repo + apps/ + legacy/ + registry (Repo Layout v1 §2-§3) ────────────────────────────────────
ALLOWED_TOP = {"apps", "services", "legacy", "packages", "infra", "tests", "docs", "tasks", ".omp", ".github", ".claude",
               "CLAUDE.md", "README.md", ".gitignore", ".gitattributes", ".env.dev", ".dev", ".ruff_cache", ".git", ".pytest_cache"}


def stray_top_level(names: list[str]) -> list[str]:
    return sorted(n for n in names if n not in ALLOWED_TOP)


def tracked_top_level() -> list[str]:
    import subprocess
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return sorted({line.split("/", 1)[0] for line in out.splitlines() if line})


def test_repo_root_only_contains_standard_entries():
    # Chỉ xét thứ ĐÃ track (file rác tạm thời do tiến trình khác tạo ở gốc không làm test chập chờn).
    assert stray_top_level(tracked_top_level()) == [],         "thư mục/tệp lạ ở gốc repo: đặt vào apps/ services/ legacy/ packages/ infra/ docs/ tests/ (xem repo-layout-v1.md)"


def test_every_app_follows_the_skeleton():
    apps = ROOT / "apps"
    assert apps.is_dir()
    problems = {}
    for d in sorted(p for p in apps.iterdir() if p.is_dir()):
        miss = [n for n in ("README.md", "package.json") if not (d / n).exists()]
        if miss:
            problems[d.name] = miss
    assert problems == {}


def test_legacy_directories_are_marked_frozen():
    legacy = ROOT / "legacy"
    for d in sorted(p for p in legacy.iterdir() if p.is_dir()):
        readme = (d / "README.md")
        assert readme.is_file() and "ĐÓNG BĂNG" in readme.read_text(encoding="utf-8"), f"legacy/{d.name} thiếu README ghi ĐÓNG BĂNG"


def test_every_registry_path_exists_and_is_under_services():
    for name, svc in load_registry()["services"].items():
        assert svc["path"].startswith("services/") and (ROOT / svc["path"]).is_dir(), (name, svc["path"])


def test_compose_stack_builds_every_service_with_a_dockerfile():
    text = (ROOT / "infra" / "compose" / "stack.yml").read_text(encoding="utf-8")
    for name, svc in load_registry()["services"].items():
        assert f"{svc['path']}/Dockerfile" in text, f"infra/compose/stack.yml thiếu {name}"


def test_top_level_checker_detects_strays():
    assert stray_top_level(["services", "admin-web", "README.md", "scripts"]) == ["admin-web", "scripts"]
