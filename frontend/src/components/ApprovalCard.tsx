import { useEffect, useState } from "react";
import { Loader2, MessageSquare, PauseCircle, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import type { Approval } from "../types";
import { asString, pretty } from "../lib/format";
import { Badge, RichText } from "./ui";

/**
 * Gate for a risky operation. The mandatory approval policy applies to every
 * destructive write. A matching user breakpoint is shown alongside it and adds
 * a feedback path, but never weakens the mandatory Approve / Reject decision.
 */
export function ApprovalCard({
  approval,
  busy,
  reason,
  onReasonChange,
  onApprove,
  onReject,
  onFeedback,
}: {
  approval: Approval;
  busy: boolean;
  reason: string;
  onReasonChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
  onFeedback: (text: string) => Promise<boolean>;
}) {
  const [feedback, setFeedback] = useState("");
  const [sending, setSending] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const matched = approval.matched_breakpoint;
  const matchedChips = matched
    ? matched.kind === "filter_excludes" && matched.condition
      ? [
          "FILTER",
          matched.condition.column.toUpperCase(),
          `EXCLUDES ${matched.condition.value.toUpperCase()}`,
        ]
      : matched.operation_types.map((operation) => operation.toUpperCase())
    : [];

  useEffect(() => {
    setFeedback("");
    setFeedbackSent(false);
    setSending(false);
  }, [approval.id]);

  const submitFeedback = async () => {
    const text = feedback.trim();
    if (!text || sending || busy) return;
    setSending(true);
    try {
      const ok = await onFeedback(text);
      if (ok) {
        setFeedback("");
        setFeedbackSent(true);
      }
    } finally {
      setSending(false);
    }
  };

  const title = matched
    ? approval.mandatory
      ? "Breakpoint matched before destructive write"
      : "Breakpoint matched before operation"
    : "Destructive write requires approval";
  const subtitle = matched
    ? approval.mandatory
      ? "Paused before execution; the mandatory approval gate still applies"
      : "Paused before execution; no operation has run"
    : "Continue, Step Over, and Step Out cannot bypass this gate";

  return (
    <section className="mb-3 overflow-hidden rounded-xl border border-amber-300 bg-amber-50/70 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-200 bg-amber-100/60 px-3.5 py-2">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-amber-500 text-white">
            <ShieldAlert size={13} />
          </span>
          <div>
            <h2 className="text-[12px] font-semibold text-amber-950">{title}</h2>
            <p className="text-[9.5px] text-amber-800">{subtitle}</p>
          </div>
        </div>
        <Badge tone="warning" className="py-0 text-[9px]">
          {matched ? "Breakpoint hit" : "Awaiting approval"}
        </Badge>
      </div>
      <div className="px-3.5 py-2.5">
        <p className="text-[11.5px] leading-relaxed text-amber-950">
          <RichText text={approval.label} />
        </p>

        {matched ? (
          <div className="mt-2 rounded-lg border border-amber-300 bg-white/90 px-2.5 py-2">
            <div className="flex items-center gap-1.5 text-[8.5px] font-semibold uppercase tracking-wide text-amber-700">
              <PauseCircle size={10} />
              Matched breakpoint rule
            </div>
            <p className="mt-0.5 text-[11px] font-semibold text-ink">{matched.label}</p>
            <p className="mt-0.5 text-[9.5px] leading-relaxed text-muted">
              “{matched.natural_language}”
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              {matchedChips.map((chip) => (
                <span
                  key={chip}
                  className="chip border-amber-200 bg-amber-50 py-0 font-mono text-[8.5px] text-amber-800"
                >
                  {chip}
                </span>
              ))}
              <span className="text-[9px] text-faint">before execution</span>
            </div>
          </div>
        ) : null}

        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-amber-200 bg-white/80 px-2.5 py-2">
            <div className="text-[8.5px] font-semibold uppercase tracking-wide text-amber-700">
              Tool
            </div>
            <div className="mt-0.5 font-mono text-[10.5px] font-semibold text-ink">
              {approval.tool}
            </div>
            <div className="mt-0.5 font-mono text-[9.5px] text-muted">
              {asString(approval.args.table ?? "sandbox_orders")}
            </div>
            <details className="mt-1">
              <summary className="cursor-pointer text-[9px] text-amber-800">
                Exact arguments
              </summary>
              <pre className="mt-1 max-h-32 overflow-auto rounded bg-amber-50 p-1.5 font-mono text-[9px] text-amber-950">
                {pretty(approval.args)}
              </pre>
            </details>
          </div>
          <div className="rounded-lg border border-amber-200 bg-white/80 px-2.5 py-2">
            <div className="text-[8.5px] font-semibold uppercase tracking-wide text-amber-700">
              Preview · not executed
            </div>
            <div className="mt-1 space-y-0.5">
              {approval.facts.map((fact, index) => (
                <div
                  key={`${fact.label}-${index}`}
                  className="flex items-baseline justify-between gap-2 text-[10px]"
                >
                  <span className="text-muted">{fact.label}</span>
                  <span className="font-semibold text-ink">{fact.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-2 flex flex-col gap-1.5 sm:flex-row sm:items-center">
          <input
            value={reason}
            onChange={(event) => onReasonChange(event.target.value)}
            placeholder="Optional rejection reason"
            className="min-w-0 flex-1 rounded-lg border border-amber-200 bg-white px-2.5 py-1.5 text-[10.5px] text-ink placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-amber-200"
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={onApprove}
              disabled={busy}
              className="btn border-amber-600 bg-amber-600 px-2.5 py-1.5 text-[10.5px] text-white hover:border-amber-700 hover:bg-amber-700"
            >
              <ShieldCheck size={12} />
              Approve & execute once
            </button>
            <button
              type="button"
              onClick={onReject}
              disabled={busy}
              className="btn-secondary px-2.5 py-1.5 text-[10.5px]"
            >
              <XCircle size={12} />
              Reject
            </button>
          </div>
        </div>

        {matched ? (
          <div className="mt-2 border-t border-amber-200 pt-2">
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center">
              <input
                value={feedback}
                onChange={(event) => {
                  setFeedback(event.target.value);
                  setFeedbackSent(false);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitFeedback();
                  }
                }}
                disabled={busy || sending}
                placeholder="Give feedback to the agent instead of approving"
                className="min-w-0 flex-1 rounded-lg border border-amber-200 bg-white px-2.5 py-1.5 text-[10.5px] text-ink placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-amber-200 disabled:opacity-60"
              />
              <button
                type="button"
                onClick={() => void submitFeedback()}
                disabled={busy || sending || !feedback.trim()}
                className="btn-secondary px-2.5 py-1.5 text-[10.5px]"
              >
                {sending ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <MessageSquare size={12} />
                )}
                Give feedback
              </button>
            </div>
            <p className="mt-1 text-[9px] leading-snug text-amber-800">
              {feedbackSent
                ? "Feedback recorded. The operation has not executed; the gate remains pending."
                : "Feedback is recorded without executing the operation. The Approve / Reject gate remains in force."}
            </p>
          </div>
        ) : null}

        <p className="mt-1.5 text-[9px] leading-snug text-amber-800">
          {approval.mandatory
            ? "Targets a disposable per-run SQLite copy. No production database is reachable."
            : "This is a user breakpoint on a non-destructive operation. Approving executes this operation once; the mandatory policy is unchanged."}
        </p>
      </div>
    </section>
  );
}
