import {
  AlertTriangle,
  Check,
  Circle,
  CircleDot,
  Loader2,
  Pause,
  ShieldAlert,
  Workflow,
  XCircle,
} from "lucide-react";
import type { StepView } from "../types";
import { cx } from "../lib/format";

function StepIcon({ step }: { step: StepView }) {
  if (step.status === "completed") return <Check size={12} strokeWidth={2.8} />;
  if (step.status === "skipped") return <XCircle size={12} />;
  if (step.status === "failed") return <AlertTriangle size={12} />;
  if (step.status === "awaiting_approval") return <ShieldAlert size={12} />;
  if (step.status === "running") return <Loader2 size={12} className="animate-spin" />;
  if (step.status === "inside") return <CircleDot size={12} />;
  if (step.status === "paused") return <Pause size={10} strokeWidth={2.5} />;
  return <Circle size={10} />;
}

function iconClasses(step: StepView): string {
  if (step.status === "completed") return "border-emerald-500 bg-emerald-500 text-white";
  if (step.status === "skipped") return "border-slate-300 bg-slate-100 text-slate-500";
  if (step.status === "failed") return "border-red-300 bg-red-50 text-red-600";
  if (step.status === "awaiting_approval") return "border-amber-400 bg-amber-50 text-amber-700";
  if (["inside", "paused", "running"].includes(step.status)) {
    return "border-accent-500 bg-accent-50 text-accent-600";
  }
  return "border-line bg-white text-faint";
}

function statusText(step: StepView): string {
  if (step.inherited) return "Inherited";
  if (step.is_current) {
    if (step.status === "inside") return "Inside";
    if (step.status === "running") return "Running";
    if (step.status === "awaiting_approval") return "Approval";
    if (step.status === "completed") return "Done";
    if (step.status === "skipped") return "Skipped";
    if (step.status === "failed") return "Failed";
    return "Current";
  }
  if (step.status === "completed") return "Done";
  if (step.status === "skipped") return "Skipped";
  if (step.status === "failed") return "Failed";
  return "Pending";
}

function statusClasses(step: StepView): string {
  if (step.status === "awaiting_approval") return "text-amber-700";
  if (step.status === "completed") return "text-emerald-700";
  if (step.status === "failed") return "text-red-600";
  if (step.status === "skipped") return "text-slate-500";
  if (step.is_current) return "text-accent-700";
  return "text-faint";
}

/**
 * The sidebar is deliberately only the agent's coarse plan. Tool calls,
 * arguments, evidence, and lineage belong to Step Inspection, not here.
 */
export function WorkflowSidebar({
  steps,
  selectedStepId,
  onSelect,
}: {
  steps: StepView[];
  selectedStepId: string | null;
  onSelect: (stepId: string) => void;
}) {
  return (
    <aside className="h-fit lg:sticky lg:top-4">
      <div className="flex items-center gap-2 px-2.5 pb-2">
        <Workflow size={14} className="text-accent-600" />
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          Workflow
        </h2>
      </div>
      <nav aria-label="Agent workflow">
        <ol className="space-y-0.5">
          {steps.map((step) => {
            const selected = step.id === selectedStepId;
            return (
              <li key={step.id}>
                <button
                  type="button"
                  onClick={() => onSelect(step.id)}
                  aria-current={selected ? "step" : undefined}
                  className={cx(
                    "group flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition",
                    selected
                      ? "bg-accent-50 ring-1 ring-inset ring-accent-200"
                      : "hover:bg-black/[0.025]",
                  )}
                >
                  <span
                    className={cx(
                      "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border",
                      iconClasses(step),
                    )}
                  >
                    <StepIcon step={step} />
                  </span>
                  <span
                    className={cx(
                      "min-w-0 flex-1 text-[12.5px] leading-snug",
                      selected || step.is_current
                        ? "font-semibold text-ink"
                        : "font-medium text-ink/85",
                    )}
                  >
                    {step.number}. {step.title}
                  </span>
                  <span
                    className={cx(
                      "mt-0.5 shrink-0 text-[9px] font-medium uppercase tracking-[0.06em]",
                      statusClasses(step),
                    )}
                  >
                    {statusText(step)}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
    </aside>
  );
}
