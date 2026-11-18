import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Database,
  FlaskConical,
  Loader2,
  Radio,
  Sparkles,
  WifiOff,
  X,
} from "lucide-react";
import type { AppConfig, RunView } from "../types";
import { cx } from "../lib/format";

const SCENARIOS = [
  {
    id: "repeat_purchase",
    label: "Repeat purchase rate",
    detail: "Semantic binding: customer identity",
    task: "What is our customer repeat purchase rate?",
    dataset: "olist",
  },
  {
    id: "category_sales",
    label: "Monthly category sales",
    detail: "Join grain & metric definition",
    task: "Show monthly product category sales from delivered orders.",
    dataset: "olist",
  },
  {
    id: "sandbox_cleanup",
    label: "Sandbox risky DELETE",
    detail: "Approval gate on disposable SQLite",
    task: "Delete canceled test orders from the disposable sandbox and verify the remaining rows.",
    dataset: "sandbox",
  },
  {
    id: "customer_cleanup",
    label: "Customer cleanup",
    detail: "Temporal reference & archival",
    task: "Clean up our customer table: delete customers who have not placed an order in the last 12 months.",
    dataset: "sandbox",
  },
];

export function NewRunDialog({
  open,
  config,
  busy,
  onClose,
  onCreate,
}: {
  open: boolean;
  config: AppConfig | null;
  busy: boolean;
  onClose: () => void;
  onCreate: (body: { task: string; mode: "scripted" | "live"; dataset: string }) => Promise<string | null>;
}) {
  const [task, setTask] = useState("What is our customer repeat purchase rate?");
  const [mode, setMode] = useState<"scripted" | "live">("scripted");
  const [dataset, setDataset] = useState("olist");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTask(config?.default_task ?? "What is our customer repeat purchase rate?");
      setMode("scripted");
      setDataset("olist");
      setError(null);
    }
  }, [open, config?.default_task]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (dataset === "sandbox" && mode === "live") setMode("scripted");
  }, [dataset, mode]);

  if (!open) return null;
  const live = config?.live;
  const liveDisabled = dataset === "sandbox" || !live?.available;
  const liveReason =
    dataset === "sandbox"
      ? "Live Agent is not enabled for the disposable SQLite sandbox in this POC."
      : live?.reason ?? "No LLM key is configured.";

  const submit = async () => {
    if (!task.trim()) {
      setError("Enter a data-analysis task.");
      return;
    }
    setError(null);
    const failure = await onCreate({ task: task.trim(), mode, dataset });
    if (failure) {
      setError(failure);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-ink/35 p-0 backdrop-blur-[2px] sm:items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-run-title"
        className="fade-in flex max-h-[92vh] w-full max-w-[620px] flex-col overflow-hidden rounded-t-2xl border border-line bg-white shadow-float sm:rounded-2xl"
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div>
            <h2 id="new-run-title" className="text-[15px] font-semibold tracking-[-0.01em]">
              Start a debugger run
            </h2>
            <p className="mt-0.5 text-[11px] text-muted">
              The plan pauses before step 1. You control every execution boundary.
            </p>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost p-1.5" aria-label="Close">
            <X size={17} />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          <label htmlFor="task" className="section-label">
            Data task
          </label>
          <textarea
            id="task"
            autoFocus
            rows={3}
            value={task}
            onChange={(event) => setTask(event.target.value)}
            className="mt-1.5 w-full resize-none rounded-xl border border-line bg-[#FBFAFD] px-3 py-2.5 text-[13px] leading-relaxed text-ink placeholder:text-faint focus:border-accent-300 focus:bg-white focus:outline-none focus:ring-2 focus:ring-accent-100"
            placeholder="Ask a data question..."
          />

          <div className="mt-3">
            <div className="section-label">Prepared scenarios</div>
            <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
              {SCENARIOS.map((scenario) => (
                <button
                  key={scenario.id}
                  type="button"
                  onClick={() => {
                    setTask(scenario.task);
                    setDataset(scenario.dataset);
                    setMode("scripted");
                  }}
                  className={cx(
                    "rounded-xl border px-2.5 py-2 text-left transition",
                    task === scenario.task
                      ? "border-accent-300 bg-accent-50"
                      : "border-line bg-white hover:border-accent-200 hover:bg-accent-50/40",
                  )}
                >
                  <span className="block text-[10.5px] font-semibold text-ink">{scenario.label}</span>
                  <span className="mt-0.5 block text-[9px] leading-snug text-muted">{scenario.detail}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <div className="section-label">Mode</div>
              <div className="mt-1.5 grid grid-cols-2 gap-1 rounded-xl border border-line bg-[#FBFAFD] p-1">
                <button
                  type="button"
                  onClick={() => setMode("scripted")}
                  className={cx(
                    "flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium transition",
                    mode === "scripted" ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink",
                  )}
                >
                  <FlaskConical size={13} />
                  Scripted
                </button>
                <button
                  type="button"
                  onClick={() => setMode("live")}
                  disabled={liveDisabled}
                  title={liveDisabled ? liveReason : "Use the configured LLM"}
                  className={cx(
                    "flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium transition",
                    mode === "live" ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink",
                    liveDisabled && "cursor-not-allowed opacity-45",
                  )}
                >
                  {live?.available && dataset !== "sandbox" ? <Radio size={13} /> : <WifiOff size={13} />}
                  Live
                </button>
              </div>
              <p className="mt-1.5 text-[9.5px] leading-snug text-faint">
                {mode === "scripted"
                  ? "Fixed plan choices, real tool execution, real data computations."
                  : `Genuine LLM planning via ${live?.provider ?? "provider"}${
                      live?.model ? ` (${live.model})` : ""
                    }.`}
              </p>
            </div>

            <div>
              <div className="section-label">Dataset</div>
              <div className="mt-1.5 space-y-1">
                {[
                  { id: "olist", label: "Olist e-commerce", icon: <Database size={13} /> },
                  { id: "sandbox", label: "Disposable SQLite sandbox", icon: <FlaskConical size={13} /> },
                ].map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setDataset(item.id)}
                    className={cx(
                      "flex w-full items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-left text-[10.5px] transition",
                      dataset === item.id
                        ? "border-accent-300 bg-accent-50 text-accent-800"
                        : "border-line bg-white text-muted hover:border-accent-200",
                    )}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {liveDisabled && mode === "live" ? (
            <div className="mt-3 flex gap-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-[10px] leading-snug text-amber-800">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              {liveReason}
            </div>
          ) : null}

          {dataset === "sandbox" ? (
            <div className="mt-3 flex gap-1.5 rounded-lg border border-amber-200 bg-amber-50/70 px-2.5 py-2 text-[10px] leading-snug text-amber-900">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              The sandbox is a per-run copy of order and customer rows. Destructive DELETE always stops at a
              mandatory approval gate. No production database is accessed.
            </div>
          ) : null}

          {error ? (
            <div className="mt-3 rounded-lg bg-red-50 px-2.5 py-2 text-[10.5px] text-red-700">{error}</div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-[#FBFAFD] px-5 py-3">
          <span className="flex items-center gap-1.5 text-[10px] text-faint">
            <Sparkles size={12} className="text-accent-500" />
            Run starts paused. Nothing executes until you step.
          </span>
          <div className="flex gap-1.5">
            <button type="button" onClick={onClose} className="btn-secondary px-3 py-1.5 text-[11px]">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={busy || !task.trim() || (mode === "live" && !live?.available)}
              className="btn-primary px-3.5 py-1.5 text-[11px]"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowRight size={13} />}
              Create run
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Onboarding({
  config,
  onNewRun,
}: {
  config: AppConfig | null;
  onNewRun: () => void;
}) {
  return (
    <div className="card mx-auto max-w-3xl overflow-hidden">
      <div className="bg-gradient-to-br from-accent-50 via-white to-[#FBFAFD] px-6 py-8 sm:px-10">
        <span className="chip border-accent-200 bg-white/80 text-accent-700">
          <Sparkles size={11} /> Successful but wrong
        </span>
        <h2 className="mt-3 max-w-xl text-2xl font-bold tracking-[-0.035em] text-ink sm:text-3xl">
          Inspect what the data agent meant, not just what it returned.
        </h2>
        <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted">
          A run can execute every operation without error and still compute the wrong answer. This debugger exposes
          explicit task semantics, real operation evidence, and checkpoint replay. It does not expose hidden
          chain-of-thought.
        </p>
        <button type="button" onClick={onNewRun} className="btn-primary mt-4 px-4 py-2 text-[12px]">
          Start a run
          <ArrowRight size={14} />
        </button>
      </div>
      <div className="grid gap-3 border-t border-line px-6 py-5 sm:grid-cols-3 sm:px-10">
        {[
          ["Interpret", "Review the metric definition, population, and field bindings before trusting results."],
          ["Inspect", "Step through real operations. Previews stay separate from observed artifacts."],
          ["Replay", "Revise via natural language, re-execute from a checkpoint, and compare both runs."],
        ].map(([title, detail]) => (
          <div key={title}>
            <div className="text-[11px] font-semibold text-ink">{title}</div>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted">{detail}</p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line bg-[#FBFAFD] px-6 py-2.5 text-[10px] text-faint sm:px-10">
        <span>
          <span className="font-medium text-muted">Scripted</span> · fixed plan, real data
        </span>
        <span>·</span>
        <span>
          <span className="font-medium text-muted">Live</span> ·{" "}
          {config?.live.available ? `configured (${config.live.provider})` : "unavailable without an API key"}
        </span>
      </div>
    </div>
  );
}
