interface DbUser {
  id: string;
  username: string;
  fetched_at: string;
  channel_count: number;
  pulled_count: number;
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
  pulled: number;
  pulled_at: string;
  imported_at: string;
  updated_at: string;
  video_count?: number;   // số link video đã lấy về máy (TikTok)
}

interface VideoLink {
  video_id: string; url: string; title: string; published_at: string; views: number | null; fetched_at: string;
}

interface SystemEndpoint {
  name: string; method: string; path: string; scope: string | null; auth: boolean; probe: "safe" | "skip"; summary: string;
}

interface SystemProbeResult {
  name: string; method: string; path: string; scope: string | null; summary: string;
  status: "ok" | "fail" | "skipped"; http: number | null; ms: number | null; detail: string;
}

interface SyncResult {
  added_users: string[]; removed_users: number;
  added_channels: number; updated_channels: number; removed_channels: number;
  skipped: { personal_tool_owners: number; personal_tool_channels: number; other_channels: number };
}

declare interface Window {
  channels?: {
    init:           () => Promise<{ ok: boolean; db_path: string; user_count: number; channel_count: number }>;
    sync:           () => Promise<SyncResult | { error: string }>;
    listUsers:      () => Promise<{ users: DbUser[] }>;
    pull:           (ids: string[]) => Promise<{ pulled: number; already: number; unknown: number }>;
    unpull:         (ids: string[]) => Promise<{ unpulled: number }>;
    listChannels:   (filters: {
      platform?: string; owner_id?: string;
      mapping_status?: string; enabled_filter?: string; pulled_filter?: string; search?: string;
    }) => Promise<{ channels: LocalChannel[] }>;
    platformCounts: () => Promise<{ total: number; youtube: number; tiktok: number; facebook: number; other: number }>;
    setEnabled:     (ids: string[], enabled: boolean) => Promise<{ updated: number }>;
    login:          (url: string, username: string, password: string) => Promise<{ ok: boolean; access_token?: string; user?: { username: string }; error?: string }>;
    apiCall:        (path: string, token: string) => Promise<unknown>;
    fetchTikTokLinks: (channelId: string, url: string) => Promise<
      { ok: true; found: number; added: number; total: number; kind: "ok" | "empty"; via: string; attempts: number; export: string } | { error: string }>;
    listVideos:     (channelId: string) => Promise<{ videos: VideoLink[] }>;
    revealLinks:    (path: string) => Promise<{ ok: boolean; error?: string }>;
    saveLinks:      (name: string, urls: string[]) => Promise<{ ok: boolean; canceled?: boolean; path?: string; error?: string }>;
  };
}
