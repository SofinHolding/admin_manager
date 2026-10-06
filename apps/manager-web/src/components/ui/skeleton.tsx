import { cn } from "../../lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-sm bg-elev-2", className)} />;
}
