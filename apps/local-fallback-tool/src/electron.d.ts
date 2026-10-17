interface DbUser {
  id: string;
  username: string;
  fetched_at: string;
  channel_count: number;
}

interface LocalChannel {
  id: string;
  owner_id: string;
  owner_username: string;
  platform: string;
  resource_type: string;
  display_name: string;
  input_ref: string;
  canonical_id: string;
  url: string;
  mapping_status: string;
  enabled: number;
  imported_at: string;
  updated_at: string;
}

interface ApiInventoryItem {
  id: string;
  owner_account_id: string;
  owner_username: string;
  platform: string;
  resource_type: string;
  canonical_external_id?: string;
  input_ref?: string;
  display_name?: string;
  url?: string;
  mapping_status?: string;
  enabled?: boolean | number;
}

interface SystemEndpoint {
  name: string; method: string; path: string; scope: string | null; auth: boolean; probe: "safe" | "skip"; summary: string;
}

interface SystemProbeResult {
  name: string; method: string; path: string; scope: string | null; summary: string;
  status: "ok" | "fail" | "skipped"; http: number | null; ms: number | null; detail: string;
}

interface SharedPull {
  owners: { id: string; username: string; resource_count: number }[];
  channels: ApiInventoryItem[];
  skipped: { personal_tool_owners: number; personal_tool_channels: number; other_channels: number };
}

declare interface Window {
  channels?: {
    init:           () => Promise<{ ok: boolean; db_path: string; user_count: number; channel_count: number }>;
    saveUsers:      (users: { id: string; username: string }[], prune?: boolean) => Promise<{ added: string[]; total: number; removed_users?: number }>;
    listUsers:      () => Promise<{ users: DbUser[] }>;
    pull:           (owner_id: string, owner_username: string, channels: ApiInventoryItem[], replace?: boolean) => Promise<{ added: number; updated: number; removed?: number }>;
    listChannels:   (filters: {
      platform?: string; owner_id?: string;
      mapping_status?: string; enabled_filter?: string; search?: string;
    }) => Promise<{ channels: LocalChannel[] }>;
    platformCounts: () => Promise<{ total: number; youtube: number; tiktok: number; facebook: number; other: number }>;
    deleteChannels: (ids: string[]) => Promise<{ deleted: number }>;
    setEnabled:     (ids: string[], enabled: boolean) => Promise<{ updated: number }>;
    login:          (url: string, username: string, password: string) => Promise<{ ok: boolean; access_token?: string; user?: { username: string }; error?: string }>;
    apiCall:        (path: string, token: string) => Promise<unknown>;
  };
}
