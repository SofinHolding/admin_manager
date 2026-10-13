interface CollectorConfig {
  gateway: string;
  api_key: string;
  youtube_api_key?: string;
  checkview_dir: string;
  poll_interval?: number;
}

declare interface Window {
  collector?: {
    start: (cfg: CollectorConfig) => Promise<{ ok: boolean; pid?: number }>;
    stop: () => Promise<{ ok: boolean }>;
    status: () => Promise<{ running: boolean; pid: number | null }>;
    readLog: () => Promise<string>;
    readConfig: () => Promise<CollectorConfig>;
    writeConfig: (cfg: CollectorConfig) => Promise<{ ok: boolean }>;
  };
}
