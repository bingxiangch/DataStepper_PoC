import { useEffect, useRef, useState } from "react";
import {
  Boxes,
  ChevronDown,
  History,
  Plus,
  Radio,
  Sparkles,
  WifiOff,
} from "lucide-react";
import type { AppConfig, RunSummary, RunView } from "../types";
import { shortId, statusTone } from "../lib/format";
import { Badge } from "./ui";

export function Header({
  config,
  view,
  runs,
  onNewRun,
  onOpenRun,
}: {
  config: AppConfig | null;
  view: RunView | null;
  runs: RunSummary[];
  onNewRun: () => void;
  onOpenRun: (id: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const live = config?.live;

  return (
    <header className="border-b border-line bg-white/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-600 text-white shadow-sm">
            <Boxes size={21} strokeWidth={2.1} />
          </div>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="truncate text-[17px] font-bold tracking-[-0.025em] sm:text-[19px]">
                Data Agent Debugger
              </h1>
              <span className="hidden rounded-full border border-line bg-[#F8F7FC] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint lg:inline">
                Research prototype
              </span>
            </div>
            <p className="hidden truncate text-xs text-muted sm:block">
              Interactive debugging of LLM data agents · successful but wrong
            </p>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {live ? (
            live.available ? (
              <span
                className="chip border-emerald-200 bg-emerald-50 text-emerald-700"
                title={`${live.provider} · ${live.model ?? "configured"}`}
              >
                <Radio size={12} />
                Live {live.provider}
              </span>
            ) : (
              <span
                className="chip border-line bg-[#F8F7FC] text-muted"
                title={live.reason ?? "No LLM key configured"}
              >
                <WifiOff size={12} />
                Live unavailable
              </span>
            )
          ) : null}

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              className="btn-secondary min-w-[124px] justify-between px-3 py-2 text-xs"
              aria-haspopup="listbox"
              aria-expanded={menuOpen}
            >
              <span className="flex items-center gap-2">
                <History size={14} />
                {view ? shortId(view.id) : "Runs"}
              </span>
              <ChevronDown size={14} className="text-faint" />
            </button>
            {menuOpen ? (
              <div className="absolute right-0 z-50 mt-2 w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-line bg-white shadow-float">
                <div className="flex items-center justify-between border-b border-line px-3.5 py-2.5">
                  <span className="section-label">Recent runs</span>
                  <span className="text-[11px] text-faint">{runs.length} total</span>
                </div>
                <div className="max-h-[320px] overflow-y-auto p-1.5">
                  {runs.length === 0 ? (
                    <p className="px-2.5 py-3 text-xs text-muted">No runs yet.</p>
                  ) : (
                    runs.slice(0, 12).map((run) => {
                      const tone = statusTone(run.status);
                      return (
                        <button
                          type="button"
                          key={run.id}
                          onClick={() => {
                            onOpenRun(run.id);
                            setMenuOpen(false);
                          }}
                          className="flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-accent-50/70"
                        >
                          <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-xs font-medium text-ink">
                              {run.task}
                            </span>
                            <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-faint">
                              <span className="font-mono">{shortId(run.id)}</span>
                              <span>·</span>
                              <span>{run.mode}</span>
                              <span>·</span>
                              <span>{tone.label}</span>
                              {run.metric_summary ? (
                                <>
                                  <span>·</span>
                                  <span className="text-muted">{run.metric_summary}</span>
                                </>
                              ) : null}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            ) : null}
          </div>

          <button type="button" onClick={onNewRun} className="btn-primary px-3 py-2 text-xs">
            <Plus size={15} />
            New run
          </button>
        </div>
      </div>

      {view ? (
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-3 gap-y-1 border-t border-line/70 px-4 py-2 text-[11px] text-muted sm:px-6">
          <span className="flex items-center gap-1.5">
            <Sparkles size={12} className="text-accent-500" />
            <span className="font-medium text-ink">{view.planner_label}</span>
          </span>
          <span className="text-line">|</span>
          <span>{view.mode === "scripted" ? "Scripted plan" : "Live agent plan"}</span>
          <span className="text-line">|</span>
          <span>
            Dataset:{" "}
            <span className="font-medium text-ink">
              {view.dataset === "sandbox" ? "Disposable SQLite sandbox" : "Olist e-commerce"}
            </span>
          </span>
          {view.parent_run_id ? (
            <>
              <span className="text-line">|</span>
              <Badge tone="accent">Replay branch of {shortId(view.parent_run_id)}</Badge>
            </>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
