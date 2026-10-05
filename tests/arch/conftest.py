import sys
from pathlib import Path

# Cho phép `import archlib` và `import scaffold_service` khi chạy từ bất kỳ thư mục nào.
HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parents[1] / "infra" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
