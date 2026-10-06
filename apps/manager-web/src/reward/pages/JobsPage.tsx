/**
 * Công việc phân phối — danh sách dễ quét: tên, trạng thái, tiến độ ("đã xong 120/300"), thời gian cập nhật.
 * Lọc theo trạng thái. Điện thoại: mỗi công việc xếp chồng thành một khối. Admin thấy thêm chủ sở hữu.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Coins, Loader2, Plus } from "lucide-react";
import { useAuth } from "../../auth";
import { jobsApi, type JobSummary } from "../api";
import { Button } from "../../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { EmptyState, Field, Notice, PageHeader } from "../../components/kit";
import { JOB_STATUS, ProgressBar, StatusBadge, fmtNumber, formatDateTime, progressSummary, relativeTime } from "./rewardUi";

/** Thứ tự trong bộ lọc: theo vòng đời công việc. */
const FILTER_ORDER = ["running", "paused", "validated", "completed", "stopped", "invalid", "draft"];

export default function JobsPage() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = async () => {
    setLoading(true);
    setFailed(false);
    try {
      const d = await jobsApi.list(status ? { status } : {});
      setJobs(d.jobs);
    } catch (err) {
      setFailed(true);
      toast.error((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const isAdmin = user?.role === "admin";
  const noFilterAndEmpty = !loading && !failed && jobs.length === 0 && !status;
  const createButton = (
    <Button asChild>
      <Link to="/reward/jobs/new"><Plus aria-hidden /> Tạo công việc</Link>
    </Button>
  );

  return (
    <div>
      <PageHeader
        title="Công việc phân phối"
        description={`Mỗi công việc gửi điểm cho một danh sách thành viên Discord. Theo dõi tiến độ và điều khiển tại đây${isAdmin ? "; quản trị viên thấy công việc của mọi người" : ""}.`}
        actions={!noFilterAndEmpty && createButton}
      />

      {!noFilterAndEmpty && (
        <div className="mb-4 max-w-xs">
          <Field label="Lọc theo trạng thái">
            {(p) => (
              <Select value={status || "all"} onValueChange={(v) => setStatus(v === "all" ? "" : v)}>
                <SelectTrigger {...p}><SelectValue placeholder="Tất cả trạng thái" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tất cả trạng thái</SelectItem>
                  {FILTER_ORDER.map((s) => <SelectItem key={s} value={s}>{JOB_STATUS[s].label}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </Field>
        </div>
      )}

      {loading ? (
        <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4" aria-hidden /> Đang tải danh sách…</p>
      ) : failed ? (
        <Notice tone="error" title="Chưa tải được danh sách công việc" action={<Button variant="outline" onClick={load}>Thử lại</Button>}>
          Kiểm tra kết nối mạng rồi bấm Thử lại.
        </Notice>
      ) : jobs.length === 0 ? (
        status ? (
          <EmptyState icon={Coins} title={`Không có công việc nào ở trạng thái "${JOB_STATUS[status]?.label ?? status}"`}
                      action={<Button variant="outline" onClick={() => setStatus("")}>Xem tất cả công việc</Button>}>
            Bỏ bộ lọc để xem các công việc khác.
          </EmptyState>
        ) : (
          <EmptyState icon={Coins} title="Bạn chưa có công việc phân phối nào" action={createButton}>
            Tạo công việc đầu tiên bằng cách dán danh sách thành viên và số điểm. Trước đó, hãy chắc rằng{" "}
            <Link to="/reward/credentials" className="font-medium text-primary-text underline underline-offset-2">kết nối Discord</Link> đã sẵn sàng.
          </EmptyState>
        )
      ) : (
        <ul className="divide-y divide-border border-y border-border" aria-label="Danh sách công việc phân phối">
          {jobs.map((job) => {
            const updated = job.finished_at ?? job.started_at ?? job.created_at;
            return (
              <li key={job.id} className="py-4">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <Link to={`/reward/jobs/${job.id}`} className="inline-flex min-h-11 min-w-0 items-center break-words text-[1.0625rem] font-semibold text-primary-text hover:underline">
                    {job.name}
                  </Link>
                  <StatusBadge status={job.status} />
                </div>
                <div className="mt-1 max-w-2xl">
                  <ProgressBar counts={job.counts} label={`Tiến độ của ${job.name}`} />
                  <p className="mt-2 text-sm">{progressSummary(job.counts)}</p>
                </div>
                <p className="mt-1 flex flex-wrap gap-x-3 text-sm text-muted-foreground">
                  <time dateTime={updated} title={formatDateTime(updated)}>Cập nhật {relativeTime(updated)}</time>
                  {job.counts.total_points != null && <span>Tổng {fmtNumber(job.counts.total_points)} điểm</span>}
                  {isAdmin && <span>Của {job.owner_username ?? "—"}</span>}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
