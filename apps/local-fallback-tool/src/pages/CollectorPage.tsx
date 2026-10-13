/**
 * Màn hình duy nhất của Tool B desktop (Electron) — điều khiển subprocess `collect.py` qua
 * `window.collector` (expose bởi `electron/preload.cjs`). Không có login: app chạy local,
 * máy người vận hành đã kiểm soát truy cập vật lý.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";

const DEFAULT_CONFIG: CollectorConfig = {
  gateway: "",
  api_key: "",
  youtube_api_key: "",
  checkview_dir: "",
  poll_interval: 15,
};

export default function CollectorPage() {
  const isElectron = !!window.collector;

  const [running, setRunning] = useState(false);
  const [pid, setPid] = useState<number | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [config, setConfig] = useState<CollectorConfig>(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [error, setError] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!isElectron) return;
    window.collector!.readConfig().then(setConfig).catch(() => {});
    window.collector!.status().then((s) => {
      setRunning(s.running);
      setPid(s.pid);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isElectron || !running) return;
    const tick = async () => {
      const s = await window.collector!.status();
      setRunning(s.running);
      setPid(s.pid);
      const raw = await window.collector!.readLog();
      setLogs(raw ? raw.split(/\r?\n/).filter((line) => line.length > 0) : []);
    };
    tick();
    const timer = setInterval(tick, 2000);
    return () => clearInterval(timer);
  }, [isElectron, running]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!isElectron) return;
    setSaving(true);
    setSaveMsg("");
    setError("");
    try {
      await window.collector!.writeConfig(config);
      setSaveMsg("Đã lưu cấu hình");
      setTimeout(() => setSaveMsg(""), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lưu cấu hình thất bại");
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async () => {
    if (!isElectron) return;
    setStarting(true);
    setError("");
    try {
      await window.collector!.writeConfig(config);
      const res = await window.collector!.start(config);
      setRunning(true);
      setPid(res.pid ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Khởi động thất bại");
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!isElectron) return;
    await window.collector!.stop();
    setRunning(false);
    setPid(null);
  };

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <header className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-4">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${running ? "bg-green-500" : "bg-red-500"}`} />
          <span className="font-semibold">Channel Collector</span>
        </div>
        {pid !== null && (
          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">PID: {pid}</span>
        )}
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 p-4">
        {!isElectron && (
          <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Mở trong Electron để dùng đầy đủ (chưa phát hiện cầu nối `window.collector`).
          </div>
        )}

        <section className="mb-4 flex items-center justify-between rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${running ? "bg-green-500" : "bg-red-500"}`} />
            <span className="font-medium">
              {running ? "RUNNING" : "STOPPED"}
              {running && <span className="ml-1 font-normal text-gray-500">(polling 2s)</span>}
            </span>
          </div>
          {running ? (
            <button
              onClick={handleStop}
              disabled={!isElectron}
              className="h-8 rounded-md border border-red-300 bg-red-50 px-3 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
            >
              Dừng (STOP)
            </button>
          ) : (
            <button
              onClick={handleStart}
              disabled={!isElectron || starting}
              className="h-8 rounded-md bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {starting ? "Đang khởi động…" : "Bắt đầu (START)"}
            </button>
          )}
        </section>

        {error && <p className="mb-3 text-xs text-red-600">{error}</p>}

        <form onSubmit={handleSave} className="mb-4 rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">Cấu hình</h2>

          <label className="mb-1 block text-xs font-medium text-gray-600">Gateway URL</label>
          <input
            value={config.gateway}
            onChange={(e) => setConfig({ ...config, gateway: e.target.value })}
            placeholder="http://127.0.0.1:57595"
            className="mb-3 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500"
          />

          <label className="mb-1 block text-xs font-medium text-gray-600">API Key</label>
          <input
            type="password"
            value={config.api_key}
            onChange={(e) => setConfig({ ...config, api_key: e.target.value })}
            placeholder="mpk_<key_id>.<secret>"
            className="mb-3 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500"
          />

          <label className="mb-1 block text-xs font-medium text-gray-600">YouTube API Key</label>
          <input
            value={config.youtube_api_key ?? ""}
            onChange={(e) => setConfig({ ...config, youtube_api_key: e.target.value })}
            placeholder="AIza..."
            className="mb-3 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500"
          />

          <label className="mb-1 block text-xs font-medium text-gray-600">CheckView Dir</label>
          <input
            value={config.checkview_dir}
            onChange={(e) => setConfig({ ...config, checkview_dir: e.target.value })}
            placeholder="D:/workspace/CheckviewV2/backend/checker"
            className="mb-3 h-9 w-full rounded-md border border-gray-300 px-3 text-sm outline-none focus:border-blue-500"
          />

          <div className="flex items-center justify-between">
            {saveMsg && <span className="text-xs text-green-600">{saveMsg}</span>}
            <button
              type="submit"
              disabled={!isElectron || saving}
              className="ml-auto h-8 rounded-md border border-gray-300 px-3 text-xs font-medium hover:bg-gray-100 disabled:opacity-50"
            >
              {saving ? "Đang lưu…" : "Lưu cấu hình"}
            </button>
          </div>
        </form>

        <section className="rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold text-gray-700">Log (cập nhật mỗi 2s)</h2>
          <pre
            ref={logRef}
            className="h-64 overflow-y-auto rounded-md bg-black p-3 font-mono text-[11px] leading-5 text-green-400"
          >
            {logs.length > 0 ? logs.join("\n") : "(chưa có log)"}
          </pre>
        </section>
      </main>
    </div>
  );
}
