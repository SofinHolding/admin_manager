import * as React from "react";
import { createPortal } from "react-dom";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Input } from "./input";
import { cn } from "../../lib/utils";

export function DateInput({
  value, onChange, min, max, title, className, onEnter, disabled,
}: {
  value: string;
  onChange: (iso: string) => void;
  min?: string;
  max?: string;
  title?: string;
  className?: string;
  onEnter?: () => void;
  disabled?: boolean;
}) {
  const [text, setText] = React.useState(() => toDisplay(value));
  const [open, setOpen] = React.useState(false);
  const boxRef = React.useRef<HTMLDivElement>(null);
  const panelRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => setText(toDisplay(value)), [value]);

  React.useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (boxRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setOpen(false); }
    function onMove() { setOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  function commit() {
    const iso = parse(text);
    if (!iso) return setText(toDisplay(value));
    const clamped = clamp(iso, min, max);
    setText(toDisplay(clamped));
    if (clamped !== value) onChange(clamped);
  }

  return (
    <div ref={boxRef} className={cn("relative", className || "w-36")}>
      <Input
        value={text}
        title={title}
        disabled={disabled}
        inputMode="numeric"
        placeholder="dd/mm/yyyy"
        maxLength={10}
        onChange={(e) => setText(mask(e.target.value))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          commit();
          onEnter?.();
        }}
        className="w-full pr-8 text-center font-mono"
      />
      <button
        type="button"
        title="Chọn ngày trên lịch"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-elev-2 hover:text-foreground"
      >
        <CalendarDays className="size-3.5" />
      </button>

      {open && (
        <Calendar
          anchor={boxRef.current}
          panelRef={panelRef}
          value={value}
          min={min}
          max={max}
          onPick={(iso) => {
            setOpen(false);
            setText(toDisplay(iso));
            if (iso !== value) onChange(iso);
          }}
        />
      )}
    </div>
  );
}

const PANEL_W = 288;
const PANEL_H = 340;

const WEEKDAYS = ["CN", "T2", "T3", "T4", "T5", "T6", "T7"];
const MONTHS = ["Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
                "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12"];

function Calendar({ anchor, panelRef, value, min, max, onPick }: {
  anchor: HTMLElement | null;
  panelRef: React.RefObject<HTMLDivElement | null>;
  value: string;
  min?: string;
  max?: string;
  onPick: (iso: string) => void;
}) {
  const today = todayIso();
  const base = value || today;
  const [y, setY] = React.useState(() => Number(base.slice(0, 4)));
  const [m, setM] = React.useState(() => Number(base.slice(5, 7)) - 1);
  const [pickMonth, setPickMonth] = React.useState(false);

  const lead = new Date(y, m, 1).getDay();
  const days = new Date(y, m + 1, 0).getDate();
  const outside = (iso: string) => (!!min && iso < min) || (!!max && iso > max);

  const r = anchor?.getBoundingClientRect();
  const left = Math.max(8, Math.min((r?.left ?? 8), window.innerWidth - PANEL_W - 8));
  const below = (r?.bottom ?? 0) + 6;
  const flipUp = below + PANEL_H > window.innerHeight && (r?.top ?? 0) > PANEL_H;
  const top = flipUp ? Math.max(8, (r?.top ?? 0) - PANEL_H - 6) : below;

  return createPortal(
    <div
      style={{ left, top }}
      ref={panelRef}
      className="fixed z-200 w-72 rounded-xl border border-hairline bg-popover p-3 text-popover-foreground shadow-2xl"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setPickMonth((v) => !v)}
          title="Chọn tháng"
          className="rounded-lg px-2 py-1 text-base font-semibold transition-colors hover:bg-elev-2"
        >
          {MONTHS[m]}
        </button>
        <div className="flex items-center gap-1">
          <NavBtn onClick={() => setY(y - 1)} title="Năm trước"><ChevronLeft className="size-4" /></NavBtn>
          <span className="min-w-[3ch] text-center text-base font-semibold tabular-nums">{y}</span>
          <NavBtn onClick={() => setY(y + 1)} title="Năm sau"><ChevronRight className="size-4" /></NavBtn>
        </div>
      </div>

      {pickMonth ? (
        <div className="grid grid-cols-3 gap-1">
          {MONTHS.map((label, i) => (
            <button
              key={label}
              type="button"
              onClick={() => { setM(i); setPickMonth(false); }}
              className={cn(
                "rounded-lg py-2 text-xs transition-colors hover:bg-elev-2",
                i === m && "bg-primary text-primary-foreground font-semibold hover:bg-primary",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-7 text-center text-[11px] text-muted-foreground">
            {WEEKDAYS.map((w) => <span key={w} className="py-1.5">{w}</span>)}
          </div>

          <div className="grid grid-cols-7 gap-y-0.5">
            {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
            {Array.from({ length: days }, (_, i) => {
              const iso = `${y}-${String(m + 1).padStart(2, "0")}-${String(i + 1).padStart(2, "0")}`;
              const disabled = outside(iso);
              const selected = iso === value;
              return (
                <div key={iso} className="flex justify-center">
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onPick(iso)}
                    className={cn(
                      "flex size-8 items-center justify-center text-[13px] tabular-nums transition-colors",
                      selected ? "rounded-full bg-primary font-semibold text-primary-foreground" : "rounded-lg",
                      !selected && iso === today && "border border-foreground/70 font-semibold",
                      disabled && "cursor-not-allowed text-muted-foreground/25",
                      !disabled && !selected && "hover:bg-elev-2",
                    )}
                  >
                    {i + 1}
                  </button>
                </div>
              );
            })}
          </div>

          <button
            type="button"
            disabled={outside(today)}
            onClick={() => onPick(today)}
            className="mt-2 w-full rounded-lg py-1.5 text-xs text-muted-foreground transition-colors hover:bg-elev-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            Hôm nay
          </button>
        </>
      )}
    </div>,
    document.body,
  );
}

function NavBtn({ onClick, title, children }: {
  onClick: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-elev-2 hover:text-foreground"
    >
      {children}
    </button>
  );
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function clamp(iso: string, min?: string, max?: string): string {
  if (min && iso < min) return min;
  if (max && iso > max) return max;
  return iso;
}

function toDisplay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

function mask(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (raw.endsWith("/") && digits.length <= 4) return raw.replace(/[^\d/]/g, "");
  const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)].filter(Boolean);
  return parts.join("/");
}

function parse(text: string): string | null {
  const m = /^(\d{1,2})\D+(\d{1,2})\D+(\d{2}|\d{4})$/.exec(text.trim());
  if (!m) return null;
  const d = Number(m[1]);
  const mo = Number(m[2]);
  const y = m[3].length === 2 ? 2000 + Number(m[3]) : Number(m[3]);
  if (!d || !mo || mo > 12 || d > 31) return null;
  const dt = new Date(y, mo - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return null;
  return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
