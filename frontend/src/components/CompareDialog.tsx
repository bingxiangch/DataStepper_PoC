import {
  ArrowRight,
  CheckCircle2,
  GitCompare,
  History,
  RotateCcw,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import type { Comparison, ReferenceCheck, RunView } from "../types";
import { asString, cx, formatDate, shortId } from "../lib/format";
import { Badge, RichText } from "./ui";

function sameToolSequence(before: string[], after: string[]): boolean {
  return before.length === after.length && before.every((tool, index) => tool === after[index]);
}

function ReferenceLine({ label, check }: { label: string; check: ReferenceCheck | null }) {
  if (!check) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-line bg-white px-2.5 py-1.5 text-[10.5px]">
        <span className="text-muted">{label}</span>
        <span className="text-faint">not available</span>
      </div>
    );
  }
  const agree = Boolean(check.agreement);
  return (
    <div
      className={cx(
        "rounded-lg border px-2.5 py-2",
        agree ? "border-emerald-200 bg-emerald-50/60" : "border-red-200 bg-red-50/50",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span className="text-[10px] font-semibold text-ink">{label}</span>
        <span
          className={cx(
            "chip py-0 text-[9px]",
            agree
              ? "border-emerald-200 bg-white text-emerald-700"
              : "border-red-200 bg-white text-red-700",
          )}
        >
          {agree ? <CheckCircle2 size={9} /> : <XCircle size={9} />}
          {agree ? "agrees" : "disagrees"}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[10px] text-muted">
        <span>
          workflow <span className="font-mono font-semibold text-ink">{check.workflow_formatted ?? "—"}</span>
        </span>
        <span>
          reference <span className="font-mono font-semibold text-ink">{check.reference_formatted ?? "—"}</span>
        </span>
        {check.mismatched_groups !== null && check.mismatched_groups !== undefined ? (
          <span>{check.mismatched_groups} mismatched group(s)</span>
        ) : null}
      </div>
    </div>
  );
}

export function CompareDialog({
  comparison,
  currentView,
  open,
  onClose,
  onOpenRun,
}: {
  comparison: Comparison | null;
  currentView: RunView | null;
  open: boolean;
  onClose: () => void;
  onOpenRun: (id: string) => void;
}) {
  if (!open || !comparison) return null;
  const parentIsCurrent = currentView?.id === comparison.parent_run_id;
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
        aria-labelledby="compare-title"
        className="fade-in flex max-h-[92vh] w-full max-w-[920px] flex-col overflow-hidden rounded-t-2xl border border-line bg-white shadow-float sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 grid h-8 w-8 place-items-center rounded-xl bg-accent-600 text-white">
              <GitCompare size={16} />
            </span>
            <div>
              <h2 id="compare-title" className="text-[15px] font-semibold tracking-[-0.01em]">
                Original run vs replay branch
              </h2>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted">
                <span className="font-mono">{shortId(comparison.parent_run_id)}</span>
                <ArrowRight size={10} />
                <span className="font-mono">{shortId(comparison.child_run_id)}</span>
                <span>· re-executed from step {comparison.affected_from_step}</span>
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost p-1.5" aria-label="Close comparison">
            <X size={17} />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {comparison.revision_explanation ? (
            <div className="rounded-xl border border-accent-200 bg-accent-50/40 px-3 py-2.5">
              <div className="section-label">Accepted revision</div>
              <p className="mt-1 text-[11.5px] leading-relaxed text-ink/90">
                <RichText text={comparison.revision_explanation} />
              </p>
            </div>
          ) : null}

          <div className="mt-4">
            <div className="section-label">Metric result · original vs replay</div>
            <div className="mt-1.5 space-y-1.5">
              {comparison.metric_definitions_diff.map((metric) => (
                <div
                  key={metric.name}
                  className="grid items-center gap-2 rounded-xl border border-line bg-white px-3 py-2.5 sm:grid-cols-[1fr_auto_1fr]"
                >
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[10px] font-semibold uppercase tracking-wide text-faint">
                        {metric.name}
                      </span>
                      <Badge tone="danger">original</Badge>
                    </div>
                    <div className="mt-1 font-mono text-lg font-bold text-ink">{metric.old_formatted}</div>
                    {metric.old_definition ? (
                      <p className="mt-0.5 line-clamp-2 text-[9.5px] leading-snug text-muted">
                        {metric.old_definition}
                      </p>
                    ) : null}
                  </div>
                  <span className="hidden justify-center text-accent-400 sm:flex">
                    <ArrowRight size={18} />
                  </span>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[10px] font-semibold uppercase tracking-wide text-faint">
                        {metric.name}
                      </span>
                      <Badge tone="observed">replay</Badge>
                    </div>
                    <div className="mt-1 font-mono text-lg font-bold text-emerald-700">{metric.new_formatted}</div>
                    {metric.new_definition ? (
                      <p className="mt-0.5 line-clamp-2 text-[9.5px] leading-snug text-muted">
                        {metric.new_definition}
                      </p>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div>
              <div className="section-label">Semantic binding changes</div>
              <div className="mt-1.5 space-y-1.5">
                {comparison.binding_diff.length === 0 ? (
                  <p className="rounded-lg border border-line px-2.5 py-2 text-[10.5px] text-faint">
                    No binding changed.
                  </p>
                ) : (
                  comparison.binding_diff.map((diff) => (
                    <div key={diff.concept} className="rounded-lg border border-line bg-white px-2.5 py-2">
                      <div className="text-[9.5px] font-medium text-faint">{diff.concept}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-[10px]">
                        <span className="rounded bg-red-50 px-1.5 py-0.5 text-red-700">{diff.old}</span>
                        <ArrowRight size={10} className="text-faint" />
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">{diff.new}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
            <div>
              <div className="section-label">Independent semantic verification</div>
              <div className="mt-1.5 space-y-1.5">
                <ReferenceLine label="Original run" check={comparison.parent_reference_check} />
                <ReferenceLine label="Replay branch" check={comparison.child_reference_check} />
              </div>
            </div>
          </div>

          <div className="mt-4">
            <div className="section-label">Execution plan changes</div>
            <div className="mt-1.5 space-y-1.5">
              {comparison.plan_diff.length === 0 ? (
                <p className="rounded-lg border border-line px-2.5 py-2 text-[10.5px] text-faint">
                  No operation list changed.
                </p>
              ) : (
                comparison.plan_diff.map((step) => {
                  const toolSequenceSame = sameToolSequence(step.old_operations, step.new_operations);
                  return (
                  <div key={`${step.step_index}-${step.title}`} className="rounded-xl border border-line bg-white px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] font-semibold text-ink">
                        Step {step.step_number} · {step.title}
                      </span>
                      {step.changed_operations ? (
                        toolSequenceSame ? (
                          <Badge tone="warning">arguments changed</Badge>
                        ) : (
                          <Badge tone="accent">operations changed</Badge>
                        )
                      ) : (
                        <Badge>explanation only</Badge>
                      )}
                      {step.changed_operations && toolSequenceSame ? (
                        <span className="text-[9.5px] text-faint">same tool sequence · arguments differ</span>
                      ) : null}
                    </div>
                    <div className="mt-1.5 grid gap-1.5 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                      <div className="flex flex-wrap gap-1">
                        {step.old_operations.map((operation, index) => (
                          <span
                            key={`${operation}-${index}`}
                            className="rounded-md bg-red-50 px-1.5 py-0.5 font-mono text-[9.5px] text-red-700"
                          >
                            {operation}
                          </span>
                        ))}
                      </div>
                      <span className="hidden text-faint sm:block">
                        <ArrowRight size={12} />
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {step.new_operations.map((operation, index) => (
                          <span
                            key={`${operation}-${index}`}
                            className="rounded-md bg-emerald-50 px-1.5 py-0.5 font-mono text-[9.5px] text-emerald-700"
                          >
                            {operation}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                  );
                })
              )}
            </div>
          </div>

          <div className="mt-4">
            <div className="section-label">Invalidated original results</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {comparison.invalidated.map((step) => (
                <span
                  key={`${step.step_number}-${step.title}`}
                  className="rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] text-amber-900"
                >
                  Step {step.step_number} · {step.title}
                  <span className="ml-1.5 text-amber-700">
                    ({step.operations.join(", ") || "no operations"})
                  </span>
                </span>
              ))}
            </div>
            <p className="mt-1.5 flex items-center gap-1 text-[9.5px] text-faint">
              <History size={10} />
              The original run and its artifacts remain immutable.
            </p>
          </div>

          {comparison.child_report ? (
            <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/40 px-3 py-2.5">
              <div className="flex items-center gap-1.5">
                <ShieldCheck size={13} className="text-emerald-700" />
                <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-800">
                  Replay report
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-ink/90">
                <RichText text={comparison.child_report.summary} />
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-[#FBFAFD] px-5 py-3">
          <span className="text-[10px] text-faint">
            Created {formatDate(currentView?.created_at)} · {comparison.invalidated.length} downstream step(s)
            invalidated
          </span>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => onOpenRun(comparison.parent_run_id)}
              disabled={parentIsCurrent}
              className="btn-secondary px-3 py-1.5 text-[11px]"
            >
              <History size={12} />
              Open original
            </button>
            <button
              type="button"
              onClick={() => onOpenRun(comparison.child_run_id)}
              disabled={!parentIsCurrent}
              className="btn-secondary px-3 py-1.5 text-[11px]"
            >
              <RotateCcw size={12} />
              Open replay
            </button>
            <button type="button" onClick={onClose} className="btn-primary px-3 py-1.5 text-[11px]">
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
