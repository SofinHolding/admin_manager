"""Tạo tài khoản admin đầu tiên — chạy 1 lần khi deploy hệ thống.

Sử dụng:
    python create_admin.py

Script sẽ hỏi username, email, mật khẩu rồi tạo tài khoản admin trực tiếp vào DB.
Không cần invite key, không cần xác thực email — đây là bootstrap cho quản trị viên gốc.

Trên VPS:
    cd /home/vilas/upload-facebook-admin
    source .env
    python create_admin.py
"""

import getpass
import os
import re
import sys


def main():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        # Mặc định SQLite cho test local
        db_url = "sqlite:///./admin.db"
        print(f"DATABASE_URL chưa đặt — dùng mặc định: {db_url}")

    print()
    print("═══ Tạo tài khoản Admin ═══")
    print()

    # Nhập thông tin
    username = input("Tên đăng nhập: ").strip()
    if not username or not re.match(r"^[a-zA-Z0-9_]{3,30}$", username):
        print("❌ Username phải 3-30 ký tự, chỉ chữ/số/gạch dưới.")
        sys.exit(1)

    email = input("Email: ").strip()
    if not email or "@" not in email:
        print("❌ Email không hợp lệ.")
        sys.exit(1)

    password = getpass.getpass("Mật khẩu (tối thiểu 8 ký tự): ")
    if len(password) < 8:
        print("❌ Mật khẩu phải có ít nhất 8 ký tự.")
        sys.exit(1)

    confirm = getpass.getpass("Xác nhận mật khẩu: ")
    if password != confirm:
        print("❌ Mật khẩu xác nhận không khớp.")
        sys.exit(1)

    import bcrypt
    import uuid
    from datetime import datetime, timezone

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    account_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if db_url.startswith("sqlite:///"):
        _create_sqlite(db_url[len("sqlite:///"):], account_id, username, email, pw_hash, now)
    elif db_url.startswith(("postgresql://", "postgres://")):
        _create_postgres(db_url, account_id, username, email, pw_hash, now)
    else:
        print(f"❌ DATABASE_URL không hỗ trợ: {db_url}")
        sys.exit(1)

    print()
    print(f"✅ Đã tạo tài khoản admin:")
    print(f"   Username: {username}")
    print(f"   Email:    {email}")
    print(f"   Role:     admin")
    print()
    print("Đăng nhập tại trang web admin để bắt đầu sử dụng.")


def _create_sqlite(path, account_id, username, email, pw_hash, now):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO accounts "
            "(id, username, email, password_hash, role, invite_key, "
            " email_verified, created_at, status) "
            "VALUES (?, ?, ?, ?, 'admin', 'BOOTSTRAP', 1, ?, 'active')",
            (account_id, username, email.lower(), pw_hash, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        if "username" in str(e).lower():
            print(f"❌ Username '{username}' đã tồn tại.")
        elif "email" in str(e).lower():
            print(f"❌ Email '{email}' đã được đăng ký.")
        else:
            print(f"❌ Lỗi: {e}")
        sys.exit(1)
    finally:
        conn.close()


def _create_postgres(dsn, account_id, username, email, pw_hash, now):
    import asyncio

    async def _run():
        import asyncpg
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO accounts "
                "(id, username, email, password_hash, role, invite_key, "
                " email_verified, created_at, status) "
                "VALUES ($1, $2, $3, $4, 'admin', 'BOOTSTRAP', true, $5, 'active')",
                account_id, username, email.lower(), pw_hash, now,
            )
        except Exception as e:
            err = str(e).lower()
            if "username" in err:
                print(f"❌ Username '{username}' đã tồn tại.")
            elif "email" in err:
                print(f"❌ Email '{email}' đã được đăng ký.")
            else:
                print(f"❌ Lỗi: {e}")
            sys.exit(1)
        finally:
            await conn.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
