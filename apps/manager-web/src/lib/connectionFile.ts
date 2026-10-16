/**
 * File kết nối (.json) cho Tool dùng chung — tải MỘT LẦN cùng lúc hiện khóa kết nối (xem SecretOnceDialog).
 * File chứa `api_key` dạng plaintext: KHÔNG lưu vào localStorage/sessionStorage, chỉ build trong bộ nhớ rồi
 * đẩy thẳng xuống máy người dùng qua Blob + thẻ `<a download>` (xem `downloadConnectionFile`).
 */

export interface ConnectionFileInput {
  gateway: string;
  toolName: string;
  clientId: string;
  keyId: string;
  apiKey: string;
  allowedPlatforms: string[];
  contractVersion: string;
  /** Mặc định là thời điểm hiện tại; truyền vào để test xác định (deterministic). */
  issuedAt?: string;
}

export interface ConnectionFile {
  format: "manager-connection/1";
  gateway: string;
  tool_name: string;
  client_id: string;
  key_id: string;
  api_key: string;
  allowed_platforms: string[];
  contract_version: string;
  issued_at: string;
}

/** Hàm THUẦN — dễ test, không chạm DOM/thời gian hệ thống trừ khi không truyền `issuedAt`. */
export function buildConnectionFile(input: ConnectionFileInput): ConnectionFile {
  return {
    format: "manager-connection/1",
    gateway: input.gateway,
    tool_name: input.toolName,
    client_id: input.clientId,
    key_id: input.keyId,
    api_key: input.apiKey,
    allowed_platforms: input.allowedPlatforms,
    contract_version: input.contractVersion,
    issued_at: input.issuedAt ?? new Date().toISOString(),
  };
}

export function connectionFileName(clientId: string): string {
  return `connection-${clientId}.json`;
}

/** Lệnh gợi ý để chạy Tool với file kết nối vừa tải. */
export function connectionFileCommand(clientId: string): string {
  return `python collect.py --connection-file ${connectionFileName(clientId)}`;
}

/** Side effect — tải file xuống máy người dùng. KHÔNG BAO GIỜ lưu `file` vào storage nào khác. */
export function downloadConnectionFile(file: ConnectionFile): void {
  const blob = new Blob([JSON.stringify(file, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = connectionFileName(file.client_id);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
