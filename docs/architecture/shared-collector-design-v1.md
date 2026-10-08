# Shared Collector Design v1 — Manager Platform

**Trạng thái:** ĐÃ THIẾT KẾ, TRIỂN KHAI PHASE 4  
**Ngày:** 2026-10-08

---

## 1. Vấn đề

User không có Tool cá nhân cần được thu thập dữ liệu kênh. Hiện tại:
- `/admin/resources` cho phép khai báo kênh nhưng cần có integration_client để commit data
- Không có cơ chế "nhờ collector thu hộ"

---

## 2. Giải pháp: Mở rộng Jobs System Hiện Có

Hệ thống jobs (`integration_db.jobs`) đã có:
- Claim với `FOR UPDATE SKIP LOCKED` (atomic, không trùng)
- Lease/heartbeat/retry/backoff
- Trạng thái (pending/running/done/dead)

**Cần thêm:**
- `job_type = 'collect'` (hiện chỉ có jobs do user/admin tạo)
- `collector_scope` để kiểm soát collector nào được claim job nào
- Auto-create jobs khi user khai báo kênh mới (với capability:manual_channel_mgmt)

---

## 3. Mô hình Multi-Tenant Collector

```
User A khai báo kênh X (platform=tiktok)
  → Integration service tạo resource(owner=A, platform=tiktok, external_id=X)
  → Auto-create job(kind='collect', resource_id=..., owner=A, platform=tiktok)

Shared Collector (client_kind='fallback_tool') claim job:
  POST /v1/integrations/jobs/claim
  body: { platforms: ["tiktok"], capabilities: ["data.collect"], limit: 50 }

  Server: job.claimed_by_client = collector_client_id
         → Collector KHÔNG biết owner=A (chỉ biết resource external_id)

Collector thu thập → gửi snapshot:
  POST /v1/integrations/snapshots
  body: { platform: "tiktok", ... }
  → owner suy từ credential (=collector's owner account)
  → Nhưng resource thuộc user A !
```

**Vấn đề ownership:** Collector dùng credential của mình (owner=system_account) nhưng data phải thuộc user A.

**Giải pháp:** Thêm `resource_owner_account_id` vào job. Khi collector commit snapshot cho job này, integration service gán `owner_account_id = job.resource_owner_account_id` thay vì credential's owner.

Đây là **"delegated ingestion"**: collector được ủy quyền push data cho owner cụ thể.

---

## 4. Security: Collector Scope

Collector chỉ được:
1. Claim jobs được phép (`allowed_platforms` trên client)
2. Push data cho resource đã được assign trong job (không tự chọn owner)
3. Không đọc user info, không access data của user khác

```sql
-- Job chứa đủ info để collector làm việc mà không cần biết user
jobs (
  id, kind='collect',
  resource_id,          -- resource cần collect
  platform,
  resource_owner_id,    -- THÊM MỚI: owner thực sự của data (≠ collector's account)
  delegated_to_client,  -- THÊM MỚI: client_id được phép claim job này (NULL = any fallback_tool)
  ...
)
```

---

## 5. Deduplication

Nếu kênh X thuộc cả User A và User B (shared channel), tránh thu thập 2 lần:

```
resource(platform=tiktok, external_id=X, owner=A) → job A
resource(platform=tiktok, external_id=X, owner=B) → job B
```

Collector: khi claim job A và job B, kiểm tra `(platform, external_id)` đã được collect trong cửa sổ T phút chưa. Nếu rồi → reuse kết quả (idempotency key chia sẻ).

Thực hiện ở collector, không phải ở server (server không biết gộp logic của tool).

---

## 6. Flow Đầy Đủ

```
1. User A khai báo kênh
   POST /v1/resources/user-channels
   → resource + job tự động

2. Shared Collector claim
   POST /v1/integrations/jobs/claim
   → nhận list jobs với resource info

3. Collector thu thập (batch nhiều kênh cùng platform)

4. Collector push kết quả
   POST /v1/integrations/snapshots (với Idempotency-Key)
   Header: X-Delegated-Job: <job_id>
   → Integration service: gán owner từ job.resource_owner_id
   → Commit snapshot

5. Job complete
   POST /v1/integrations/jobs/{id}/complete

6. Analytics consumer nhận snapshot.committed event
   → cập nhật read model với owner=A

7. User A thấy data trên dashboard
```

---

## 7. Worker Registration

```
POST /v1/integrations/workers/register
{
  "worker_id": "collector-01",
  "capabilities": ["data.collect"],
  "platforms": ["tiktok", "facebook"]
}
→ { "worker_token": "...", "heartbeat_interval_s": 30 }
```

Heartbeat: `POST /v1/integrations/workers/{id}/heartbeat` mỗi 30s.  
Nếu heartbeat miss > 3 lần → worker offline → jobs bị release về queue.

---

## 8. Thứ tự triển khai (PHASE 4)

1. Thêm `resource_owner_id`, `delegated_to_client` vào jobs (migration)
2. Endpoint tạo resource + job cho user không có Tool: `POST /v1/resources/subscriptions`
3. Delegated ingestion: header `X-Delegated-Job` trên snapshot endpoint
4. Worker registration + heartbeat
5. Collector dedup logic (trong collector SDK)
6. Frontend: `/channels` page cho website-only users
