/**
 * Nền tảng — trang riêng (route /admin/platforms) cho admin/dash_admin quản lý danh mục nền tảng (Platform Registry).
 *
 * Mỗi nền tảng (tên hiển thị) có MỘT mã chính và nhiều mã phụ. Tool gửi dữ liệu kèm một mã bất kỳ trong số đó; server chuẩn hoá về mã
 * chính nên Dashboard gom đúng nền tảng. Mã (chính + phụ) là DUY NHẤT toàn hệ thống: trang chặn nhập trùng ở phía giao diện và server
 * chặn lại bằng 409 `PLATFORM_CODE_EXISTS`. Không có nút xóa nền tảng — chỉ bật/tắt; mã phụ thì gỡ được.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Pencil, Plus, Save, X } from "lucide-react";
import { integrationApi, platformsApi, type PlatformRegistryEntry } from "../api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Badge } from "../components/ui/badge";
import { Field, PageHeader, Section } from "../components/kit";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}
const CODE_RE = /^[a-z][a-z0-9_]{0,63}$/;

/** Tách ô nhập "mã phụ" (cách nhau bằng dấu phẩy/khoảng trắng/chấm phẩy) thành danh sách mã chữ thường, bỏ trùng. */
function parseCodes(text: string): string[] {
  return [...new Set(text.toLowerCase().split(/[\s,;]+/).filter(Boolean))];
}

export default function PlatformsPage() {
  const [entries, setEntries] = useState<PlatformRegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [newKey, setNewKey] = useState("");
  const [newAliases, setNewAliases] = useState("");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [aliasDraft, setAliasDraft] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { items } = await integrationApi.listPlatformRegistry();
      setEntries(items);
    } catch (e) {
      toast.error(errMsg(e, "Không tải được danh sách nền tảng."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  /** Mọi mã đang dùng (chính + phụ) -> tên nền tảng giữ mã đó. */
  const taken = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of entries) {
      m.set(e.key, e.display_name);
      for (const a of e.aliases ?? []) m.set(a, e.display_name);
    }
    return m;
  }, [entries]);

  /** Lỗi của một mã sắp thêm; chuỗi rỗng = hợp lệ. */
  const codeProblem = (code: string): string => {
    if (!CODE_RE.test(code)) return `Mã "${code}" sai định dạng (chữ thường, số, gạch dưới; bắt đầu bằng chữ).`;
    const owner = taken.get(code);
    return owner ? `Mã "${code}" đã thuộc nền tảng "${owner}".` : "";
  };

  // ── Biểu mẫu tạo nền tảng ──
  const keyText = newKey.trim();
  const aliasList = parseCodes(newAliases);
  const createProblem = (() => {
    if (!keyText && aliasList.length === 0) return "";
    if (keyText && codeProblem(keyText)) return codeProblem(keyText);
    if (aliasList.includes(keyText)) return `Mã phụ "${keyText}" trùng mã chính.`;
    for (const a of aliasList) if (codeProblem(a)) return codeProblem(a);
    return "";
  })();
  const canCreate = !creating && !!keyText && !!newName.trim() && !createProblem;

  const handleCreate = async () => {
    if (!canCreate) return;
    setCreating(true);
    try {
      await platformsApi.createPlatform({ key: keyText, display_name: newName.trim(), aliases: aliasList });
      toast.success(`Đã thêm nền tảng "${newName.trim()}"`);
      setNewKey(""); setNewAliases(""); setNewName("");
      await load();
    } catch (e) {
      toast.error(errMsg(e, "Không thêm được nền tảng."));
      await load();   // mã có thể vừa bị người khác tạo: tải lại để chặn trùng đúng
    } finally {
      setCreating(false);
    }
  };

  // ── Sửa tên / bật-tắt ──
  const handleSaveName = async (entry: PlatformRegistryEntry) => {
    if (!editName.trim()) return;
    setBusyKey(entry.key);
    try {
      await platformsApi.putPlatform(entry.key, { display_name: editName.trim(), enabled: entry.enabled });
      setEntries((prev) => prev.map((x) => (x.key === entry.key ? { ...x, display_name: editName.trim() } : x)));
      setEditKey(null);
      toast.success("Đã lưu tên hiển thị");
    } catch (e) {
      toast.error(errMsg(e, "Không lưu được tên hiển thị."));
    } finally {
      setBusyKey(null);
    }
  };

  const handleToggleEnabled = async (entry: PlatformRegistryEntry) => {
    setBusyKey(entry.key);
    try {
      await platformsApi.putPlatform(entry.key, { display_name: entry.display_name, enabled: !entry.enabled });
      setEntries((prev) => prev.map((x) => (x.key === entry.key ? { ...x, enabled: !x.enabled } : x)));
      toast.success(entry.enabled ? "Đã tắt nền tảng" : "Đã bật nền tảng");
    } catch (e) {
      toast.error(errMsg(e, "Không đổi được trạng thái nền tảng."));
    } finally {
      setBusyKey(null);
    }
  };

  // ── Mã phụ ──
  const handleAddAlias = async (entry: PlatformRegistryEntry) => {
    const code = (aliasDraft[entry.key] ?? "").trim().toLowerCase();
    if (!code || codeProblem(code)) return;
    setBusyKey(entry.key);
    try {
      const updated = await platformsApi.addAlias(entry.key, code);
      setEntries((prev) => prev.map((x) => (x.key === entry.key ? { ...x, aliases: updated.aliases ?? [...(x.aliases ?? []), code] } : x)));
      setAliasDraft((d) => ({ ...d, [entry.key]: "" }));
      toast.success(`Đã thêm mã "${code}" cho ${entry.display_name}`);
    } catch (e) {
      toast.error(errMsg(e, "Không thêm được mã."));
      await load();
    } finally {
      setBusyKey(null);
    }
  };

  const handleRemoveAlias = async (entry: PlatformRegistryEntry, alias: string) => {
    setBusyKey(entry.key);
    try {
      await platformsApi.removeAlias(entry.key, alias);
      setEntries((prev) => prev.map((x) => (x.key === entry.key ? { ...x, aliases: (x.aliases ?? []).filter((a) => a !== alias) } : x)));
      toast.success(`Đã gỡ mã "${alias}"`);
    } catch (e) {
      toast.error(errMsg(e, "Không gỡ được mã."));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Nền tảng"
        description="Danh mục nền tảng (vd YouTube, TikTok, Facebook, hoặc nền tảng mới) mà người dùng bắt buộc chọn khi thêm kênh. Dashboard dùng đúng nền tảng này để tính số liệu."
      />
      <Section
        title="Danh mục nền tảng"
        description="Mỗi nền tảng có một mã chính và có thể có thêm mã phụ. Tool gửi dữ liệu kèm một mã bất kỳ của nền tảng thì số liệu được gom về đúng nền tảng đó trên Dashboard. Mã không được trùng nhau giữa các nền tảng."
      >
        {loading ? (
          <p className="py-6 text-muted-foreground">Đang tải…</p>
        ) : (
          <>
            {entries.length === 0 ? (
              <p className="mb-4 text-muted-foreground">Chưa có nền tảng nào. Thêm nền tảng đầu tiên bằng biểu mẫu bên dưới.</p>
            ) : (
              <ul className="mb-4 divide-y divide-border border-y border-border">
                {entries.map((entry) => {
                  const busy = busyKey === entry.key;
                  const draft = (aliasDraft[entry.key] ?? "").trim().toLowerCase();
                  const draftProblem = draft ? codeProblem(draft) : "";
                  return (
                    <li key={entry.key} className="py-2.5">
                      <div className="flex flex-wrap items-center gap-3">
                        <div className="min-w-0 flex-1">
                          {editKey === entry.key ? (
                            <span className="flex flex-wrap items-center gap-2">
                              <Input value={editName} maxLength={100} onChange={(e) => setEditName(e.target.value)}
                                     className="h-8 w-48" aria-label={`Tên hiển thị cho ${entry.key}`} />
                              <Button size="sm" variant="outline" disabled={busy || !editName.trim()}
                                      onClick={() => void handleSaveName(entry)}>
                                <Save aria-hidden className="size-4" /> Lưu
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => setEditKey(null)}>Hủy</Button>
                            </span>
                          ) : (
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="font-semibold">{entry.display_name}</span>
                              <Badge variant={entry.enabled ? "success" : "secondary"}>{entry.enabled ? "Đang bật" : "Đã tắt"}</Badge>
                            </span>
                          )}
                        </div>
                        {editKey !== entry.key && (
                          <Button size="sm" variant="outline" onClick={() => { setEditKey(entry.key); setEditName(entry.display_name); }}
                                  aria-label={`Sửa tên hiển thị ${entry.display_name}`}>
                            <Pencil aria-hidden className="size-4" /> Sửa tên
                          </Button>
                        )}
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => void handleToggleEnabled(entry)}>
                          {entry.enabled ? "Tắt" : "Bật"}
                        </Button>
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        <span className="text-muted-foreground">Mã:</span>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono" title="Mã chính — không đổi được">{entry.key}</code>
                        {(entry.aliases ?? []).map((a) => (
                          <span key={a} className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 font-mono">
                            {a}
                            <button type="button" disabled={busy} onClick={() => void handleRemoveAlias(entry, a)}
                                    aria-label={`Gỡ mã ${a} khỏi ${entry.display_name}`}
                                    className="cursor-pointer text-muted-foreground hover:text-destructive disabled:opacity-50">
                              <X aria-hidden className="size-3" />
                            </button>
                          </span>
                        ))}
                        <form className="inline-flex items-center gap-1" noValidate
                              onSubmit={(e) => { e.preventDefault(); void handleAddAlias(entry); }}>
                          <Input value={aliasDraft[entry.key] ?? ""} maxLength={64} placeholder="thêm mã phụ"
                                 aria-label={`Thêm mã cho ${entry.display_name}`}
                                 onChange={(e) => setAliasDraft((d) => ({ ...d, [entry.key]: e.target.value.toLowerCase() }))}
                                 className="h-7 w-32 font-mono text-xs" />
                          <Button type="submit" size="sm" variant="outline" disabled={busy || !draft || !!draftProblem}
                                  aria-label={`Thêm mã vào ${entry.display_name}`}>
                            <Plus aria-hidden className="size-3" /> Thêm mã
                          </Button>
                        </form>
                      </div>
                      {draftProblem && <p role="alert" className="mt-1 text-xs text-destructive">{draftProblem}</p>}
                    </li>
                  );
                })}
              </ul>
            )}

            <form className="flex max-w-3xl flex-wrap items-end gap-3" noValidate
                  onSubmit={(e) => { e.preventDefault(); void handleCreate(); }}>
              <Field label="Mã nền tảng" hint="Mã chính. Chữ thường, số, gạch dưới; bắt đầu bằng chữ. Không đổi được sau khi tạo.">
                {(p) => <Input {...p} value={newKey} maxLength={64} className="w-40 font-mono"
                               onChange={(e) => setNewKey(e.target.value.toLowerCase())} placeholder="kwai" />}
              </Field>
              <Field label="Mã phụ" optional hint="Cách nhau bằng dấu phẩy. Ví dụ: kwai_vn, kwai_app">
                {(p) => <Input {...p} value={newAliases} className="w-56 font-mono"
                               onChange={(e) => setNewAliases(e.target.value)} placeholder="kwai_vn, kwai_app" />}
              </Field>
              <Field label="Tên hiển thị">
                {(p) => <Input {...p} value={newName} maxLength={100} className="w-48"
                               onChange={(e) => setNewName(e.target.value)} placeholder="Kwai" />}
              </Field>
              <Button type="submit" disabled={!canCreate}>
                <Plus aria-hidden /> Thêm nền tảng
              </Button>
            </form>
            {createProblem && <p role="alert" className="mt-2 text-sm text-destructive">{createProblem}</p>}
          </>
        )}
      </Section>
    </div>
  );
}
