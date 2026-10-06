# apps/manager-web

Giao diện quản trị/Dashboard của Manager Platform (React + Vite + TypeScript). Gọi API qua gateway (`/v1/*`), không chứa logic nghiệp vụ backend.

- Dev: `cp .env.example .env && npm ci && npm run dev` (bind `127.0.0.1:5174`, proxy `/v1`, `/oauth`, `/.well-known` → gateway `:8400`).
- Cổng chất lượng: `npm run typecheck && npm test && npm run build` (`postbuild` quét bundle không còn secret).
- Đăng nhập: legacy hoặc OIDC PKCE (`VITE_AUTH_MODE`). Cấu trúc mã và quy tắc: `docs/handbook/03-dich-vu.md` §3.6.
- Tên package npm vẫn là `admin-web` (giữ lockfile ổn định).
