/**
 * Danh sách job — bảng tên, trạng thái, tiến độ, tổng điểm, thời gian. Admin thấy thêm cột chủ sở hữu.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Loader2, PlusCircle } from "lucide-react";
import { useAuth } from "../../auth";
import { jobsApi, type JobSummary } from "../api";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";

const STATUS_LABEL: Record<string, { label: string; variant: "default" | "success" | "destructive" | "warning" | "secondary" }> = {
  draft: { label: "Nháp", variant: "secondary" },
  invalid: { label: "Không hợp lệ", variant: "destructive" },
  validated: { label: "Đã kiểm tra", variant: "default" },
  running: { label: "Đang chạy", variant: "success" },
  paused: { label: "Tạm dừng", variant: "warning" },
  stopped: { label: "Đã dừng", variant: "secondary" },
  completed: { label: "Hoàn tất", variant: "success" },
};

function ProgressBar({ counts }: { counts: JobSummary["counts"] }) {
  const total = counts.total || 1;
  const seg = (n: number) => `${(n / total) * 100}%`;
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-elev-2">
      <div className="bg-success" style={{ width: seg(counts.success) }} title={`Thành công: ${counts.success}`} />
      <div className="bg-destructive" style={{ width: seg(counts.failed) }} title={`Thất bại: ${counts.failed}`} />
      <div className="bg-warning" style={{ width: seg(counts.unknown) }} title={`Không rõ: ${counts.unknown}`} />
      <div className="bg-elev-1" style={{ width: seg(counts.pending) }} title={`Chờ: ${counts.pending}`} />
    </div>
  );
}

export default function JobsPage() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const d = await jobsApi.list(status ? { status } : {});
      setJobs(d.jobs);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Job phân phối điểm</h1>
          <p className="text-sm text-muted-foreground">Danh sách job của bạn{user?.role === "admin" ? " và toàn hệ thống" : ""}.</p>
        </div>
        <Button asChild>
          <Link to="/reward/jobs/new"><PlusCircle className="size-4" /> Tạo job</Link>
        </Button>
      </div>

      <div className="w-48">
        <Select value={status || "all"} onValueChange={(v) => setStatus(v === "all" ? "" : v)}>
          <SelectTrigger><SelectValue placeholder="Tất cả trạng thái" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Tất cả trạng thái</SelectItem>
            {Object.keys(STATUS_LABEL).map((s) => (
              <SelectItem key={s} value={s}>{STATUS_LABEL[s].label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Đang tải…</div>
      ) : jobs.length === 0 ? (
        <div className="glass rounded-xl p-8 text-center text-sm text-muted-foreground">Chưa có job nào.</div>
      ) : (
        <div className="glass overflow-auto rounded-xl">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-2 text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Tên</th>
                <th className="px-4 py-2">Trạng thái</th>
                <th className="px-4 py-2">Tiến độ</th>
                <th className="px-4 py-2">Tổng điểm</th>
                {user?.role === "admin" && <th className="px-4 py-2">Chủ sở hữu</th>}
                <th className="px-4 py-2">Tạo lúc</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const s = STATUS_LABEL[job.status] ?? { label: job.status, variant: "secondary" as const };
                return (
                  <tr key={job.id} className="border-t border-hairline hover:bg-elev-1">
                    <td className="px-4 py-2">
                      <Link to={`/reward/jobs/${job.id}`} className="font-medium text-primary-text hover:underline">{job.name}</Link>
                    </td>
                    <td className="px-4 py-2"><Badge variant={s.variant === "default" ? undefined : s.variant}>{s.label}</Badge></td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <div className="w-32"><ProgressBar counts={job.counts} /></div>
                        <span className="text-xs text-muted-foreground">{job.counts.success}/{job.counts.total}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2">{job.counts.total_points ?? "—"}</td>
                    {user?.role === "admin" && <td className="px-4 py-2">{job.owner_username ?? "—"}</td>}
                    <td className="px-4 py-2 text-xs text-muted-foreground">{new Date(job.created_at).toLocaleString("vi-VN")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
