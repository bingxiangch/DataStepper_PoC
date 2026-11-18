import { useMemo, useRef, useState } from "react";
import {
  Bot,
  Check,
  ChevronRight,
  Info,
  Loader2,
  MessageSquare,
  RotateCcw,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  User,
  X,
} from "lucide-react";
import type { Message, Proposal, RevisionChange, RunView, StepView } from "../types";
import { cx } from "../lib/format";
import {
  describeAction,
  groupPlanOperations,
  planOperationsFrom,
  type ActionGroup,
} from "../lib/stepPresentation";
import { Badge, RichText } from "./ui";

function proposalStatusBadge(proposal: Proposal) {
  if (proposal.status === "accepted") {
    return (
      <Badge tone="observed">
        <Check size={10} /> Accepted
      </Badge>
    );
  }
  if (proposal.status === "rejected") {
    return (
      <Badge>
        <X size={10} /> Rejected
      </Badge>
    );
  }
  if (proposal.status === "no_change") {
    return <Badge tone="warning">No change</Badge>;
  }
  return <Badge tone="accent">Awaiting decision</Badge>;
}

function proposalUpdate(proposal: Proposal, step: StepView) {
  return (
    proposal.step_updates.find((update) => update.step_id === step.id) ??
    proposal.step_updates[0] ??
    null
  );
}

function proposedBinding(proposal: Proposal, view: RunView) {
  const bindings = proposal.proposed_bindings ?? [];
  const revised = bindings.find((binding) => binding.status === "revised");
  if (revised) return revised;
  return (
    bindings.find((binding) => {
      const current = view.task_spec.semantic_bindings.find(
        (candidate) => candidate.concept === binding.concept,
      );
      return (
        !current ||
        current.source_table !== binding.source_table ||
        current.column !== binding.column
      );
    }) ?? null
  );
}

const COLUMN_REFERENCE = /[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*/;

/** Keep the compact binding row to the column reference when one is present. */
function conciseBindingValue(value: string): string {
  const match = value.match(COLUMN_REFERENCE);
  return match ? match[0] : value || "—";
}

function bindingChangeConcept(change: RevisionChange, proposal: Proposal): string | null {
  const target = conciseBindingValue(change.after);
  if (target === change.after) return null;
  const binding = (proposal.proposed_bindings ?? []).find(
    (candidate) => `${candidate.source_table}.${candidate.column}` === target,
  );
  return binding?.concept ?? null;
}

/** One sentence of rationale for the compact card; the rest stays in details. */
function compactReason(proposal: Proposal, interpretation: string): string | null {
  const normalized = proposal.explanation.replace(/\s+/g, " ").trim();
  if (!normalized || normalized === interpretation) return null;
  const sentences = normalized
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  const sentence =
    sentences.find((candidate) => /\bbecause\b/i.test(candidate)) ??
    sentences.find((candidate) => /,\s*so\b/i.test(candidate)) ??
    sentences[0];
  if (!sentence) return null;
  let text = sentence;
  // Drop evidence that follows a "so" clause; the full text stays in details.
  const so = text.search(/,\s*so\b/i);
  if (so >= 0) {
    const colon = text.indexOf(":", so);
    if (colon > so) text = text.slice(0, colon).trim();
  }
  return text.length > 220 ? `${text.slice(0, 217).trimEnd()}…` : text;
}

function fallbackBindingChange(
  proposal: Proposal,
  view: RunView,
): { concept: string; description: string; before: string; after: string } | null {
  const next = proposedBinding(proposal, view);
  if (!next) return null;
  const current = view.task_spec.semantic_bindings.find(
    (candidate) => candidate.concept === next.concept,
  );
  const after = `${next.source_table}.${next.column}`;
  const before = current ? `${current.source_table}.${current.column}` : "—";
  if (before === after) return null;
  return {
    concept: next.concept,
    description: next.note || `${next.concept} changes to ${after}.`,
    before,
    after,
  };
}

function RevisionDetails({
  proposal,
  interpretation,
  actions,
}: {
  proposal: Proposal;
  interpretation: string;
  actions: ActionGroup[];
}) {
  const showExplanation =
    Boolean(proposal.explanation) &&
    proposal.explanation.replace(/\s+/g, " ").trim() !== interpretation;
  const hasDetails =
    showExplanation ||
    Boolean(interpretation) ||
    proposal.changes.length > 0 ||
    actions.length > 0 ||
    proposal.investigations.length > 0;
  if (!hasDetails) return null;

  return (
    <details className="group mt-2">
      <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-[10px] font-medium text-faint transition hover:text-accent-700">
        <ChevronRight
          size={10}
          className="transition-transform group-open:rotate-90"
        />
        Show revision details
      </summary>
      <div className="mt-1.5 space-y-2 text-[10.5px] leading-relaxed text-muted">
        {interpretation ? (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-[0.09em] text-faint">
              Revised interpretation
            </p>
            <p className="mt-0.5 text-[11px] text-ink/85">
              <RichText text={interpretation} />
            </p>
          </div>
        ) : null}
        {showExplanation ? (
          <p>
            <span className="font-medium text-ink">Agent explanation: </span>
            <RichText text={proposal.explanation} />
          </p>
        ) : null}
        {proposal.changes.length > 0 ? (
          <ul className="space-y-1">
            {proposal.changes.map((change, index) => (
              <li key={`${change.kind}-${index}`}>
                <span className="text-[8.5px] font-semibold uppercase tracking-wide text-accent-700">
                  {change.kind}
                </span>{" "}
                <span className="text-ink/85">
                  <RichText text={change.description} />
                </span>
                {change.before || change.after ? (
                  <span className="mt-0.5 block text-[10px]">
                    <span className="rounded bg-red-50 px-1 py-0.5 text-red-700">
                      {change.before || "—"}
                    </span>
                    <span className="mx-1 text-faint">→</span>
                    <span className="rounded bg-emerald-50 px-1 py-0.5 text-emerald-700">
                      {change.after || "—"}
                    </span>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {actions.length > 0 ? (
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-[0.09em] text-faint">
              Revised operations
            </p>
            <ol className="mt-1 list-decimal space-y-1 pl-5 text-[10.5px] leading-relaxed text-ink/85 marker:text-[9px] marker:font-semibold marker:text-faint">
              {actions.map((action, index) => (
                <li key={`${action.key}-${index}`}>
                  <RichText text={describeAction(action)} />
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {proposal.investigations.length > 0 ? (
          <div className="space-y-1">
            {proposal.investigations.map((investigation, index) => (
              <p key={`${investigation.tool}-${index}`}>
                <span className="font-medium text-ink">
                  Re-read via {investigation.tool}:{" "}
                </span>
                <RichText text={investigation.summary} />
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function ProposalCard({
  proposal,
  view,
  step,
  busy,
  onAccept,
  onReject,
  onReplay,
  onMoreFeedback,
}: {
  proposal: Proposal;
  view: RunView;
  step: StepView;
  busy: boolean;
  onAccept: (proposal: Proposal) => void;
  onReject: (proposal: Proposal) => void;
  onReplay: () => void;
  onMoreFeedback: () => void;
}) {
  const isPending = proposal.status === "pending";
  const affected = view.steps[proposal.earliest_affected_step];
  const update = proposalUpdate(proposal, step);
  const interpretation = (
    update?.understanding ||
    update?.goal ||
    proposal.explanation ||
    ""
  )
    .replace(/\s+/g, " ")
    .trim();
  const actions = groupPlanOperations(planOperationsFrom(update?.operations));
  const bindingChanges = proposal.changes.filter((change) => change.kind === "binding");
  const fallbackBinding =
    bindingChanges.length === 0 ? fallbackBindingChange(proposal, view) : null;
  const summaryChanges = proposal.changes
    .filter((change) => change.kind !== "binding")
    .slice(0, 3);
  const reason = compactReason(proposal, interpretation);

  return (
    <div
      className={cx(
        "rounded-xl border px-3.5 py-3",
        isPending
          ? "border-accent-200 bg-accent-50/40"
          : proposal.status === "accepted"
            ? "border-emerald-200 bg-emerald-50/35"
            : "border-line bg-[#FBFAFD]",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Sparkles size={13} className="text-accent-600" />
        <span className="text-[11.5px] font-semibold text-ink">Revised proposal</span>
        {proposalStatusBadge(proposal)}
        {affected ? (
          <span className="text-[10px] text-faint">Affects step {affected.number}</span>
        ) : null}
      </div>

      {bindingChanges.length > 0 || fallbackBinding ? (
        <div className="mt-2 rounded-lg border border-line/70 bg-white/80 px-2.5 py-2">
          <p className="text-[9px] font-semibold uppercase tracking-[0.09em] text-faint">
            Changed semantic binding
          </p>
          <div className="mt-1 space-y-1.5">
            {bindingChanges.map((change, index) => {
              const concept = bindingChangeConcept(change, proposal);
              return (
                <div key={`${change.description}-${index}`}>
                  {concept ? (
                    <p className="text-[10px] font-semibold text-ink">{concept}</p>
                  ) : null}
                  <p className="text-[10px] leading-snug text-muted">
                    <RichText text={change.description} />
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1 font-mono text-[10px] font-semibold">
                    <span className="max-w-full break-words rounded bg-red-50 px-1.5 py-0.5 text-red-700">
                      {conciseBindingValue(change.before)}
                    </span>
                    <span className="text-faint">→</span>
                    <span className="max-w-full break-words rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">
                      {conciseBindingValue(change.after)}
                    </span>
                  </div>
                </div>
              );
            })}
            {fallbackBinding ? (
              <div>
                <p className="text-[10px] font-semibold text-ink">{fallbackBinding.concept}</p>
                <p className="text-[10px] leading-snug text-muted">
                  <RichText text={fallbackBinding.description} />
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-1 font-mono text-[10px] font-semibold">
                  <span className="rounded bg-red-50 px-1.5 py-0.5 text-red-700">
                    {conciseBindingValue(fallbackBinding.before)}
                  </span>
                  <span className="text-faint">→</span>
                  <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">
                    {conciseBindingValue(fallbackBinding.after)}
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {summaryChanges.length > 0 ? (
        <div className="mt-2">
          <p className="text-[9px] font-semibold uppercase tracking-[0.09em] text-faint">
            What changes
          </p>
          <ul className="mt-1 space-y-1 text-[10.5px] leading-snug text-ink/85">
            {summaryChanges.map((change, index) => (
              <li key={`${change.description}-${index}`} className="flex gap-1.5">
                <span className="mt-[4px] h-1 w-1 shrink-0 rounded-full bg-accent-400" />
                <span>
                  <RichText text={change.description} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {reason ? (
        <p className="mt-2 text-[10.5px] leading-snug text-muted">
          <span className="text-[9px] font-semibold uppercase tracking-[0.09em] text-faint">
            Why
          </span>{" "}
          <span className="text-ink/85">
            <RichText text={reason} />
          </span>
        </p>
      ) : null}

      <RevisionDetails
        proposal={proposal}
        interpretation={interpretation}
        actions={actions}
      />

      {isPending ? (
        <div className="mt-2.5 border-t border-line/60 pt-2.5">
          {!proposal.execution_changed ? (
            <p className="mb-2 flex gap-1.5 text-[10px] leading-snug text-amber-800">
              <Info size={11} className="mt-0.5 shrink-0" />
              This proposal would not change real execution, so Accept is unavailable. Ask a
              more specific question to get an executable revision.
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => onAccept(proposal)}
              disabled={busy || !proposal.execution_changed}
              className="btn-primary px-2.5 py-1.5 text-[11px]"
            >
              <ThumbsUp size={12} />
              Accept revision
            </button>
            <button
              type="button"
              onClick={() => onReject(proposal)}
              disabled={busy}
              className="btn-secondary px-2.5 py-1.5 text-[11px]"
            >
              <ThumbsDown size={12} />
              Reject revision
            </button>
            <button
              type="button"
              onClick={onMoreFeedback}
              disabled={busy}
              className="btn-ghost px-2 py-1.5 text-[11px]"
            >
              <MessageSquare size={12} />
              More feedback
            </button>
          </div>
        </div>
      ) : null}

      {proposal.status === "rejected" ? (
        <p className="mt-2 text-[10.5px] leading-snug text-muted">
          Revision rejected. The recorded interpretation and plan are unchanged.
        </p>
      ) : null}

      {proposal.status === "accepted" ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10.5px] leading-snug text-emerald-800">
          <span>
            {view.capabilities.replay
              ? "Accepted. Replay from the checkpoint to re-execute this work and compare."
              : view.accepted_revision_id === proposal.id && view.child_run_ids.length > 0
                ? "Accepted. Replay completed in a new branch; the original run remains immutable."
                : "Accepted. The revision was applied to the pending checkpoint state."}
          </span>
          {view.capabilities.replay ? (
            <button
              type="button"
              onClick={onReplay}
              disabled={busy}
              className="btn-secondary ml-auto px-2.5 py-1 text-[10px]"
            >
              <RotateCcw size={11} />
              Replay
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  if (message.role === "system") {
    return (
      <p className="text-center text-[9.5px] text-faint">
        <RichText text={message.text} />
      </p>
    );
  }
  const isUser = message.role === "user";
  return (
    <div className={cx("flex gap-2", isUser ? "justify-end" : "justify-start")}>
      {!isUser ? (
        <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-accent-200 bg-accent-50 text-accent-700">
          <Bot size={12} />
        </span>
      ) : null}
      <div
        className={cx(
          "max-w-[86%] rounded-xl px-3 py-2 text-[11.5px] leading-relaxed",
          isUser
            ? "rounded-tr-sm bg-accent-600 text-white"
            : "rounded-tl-sm border border-line bg-white text-ink/90",
        )}
      >
        <RichText text={message.text} />
      </div>
      {isUser ? (
        <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line bg-white text-muted">
          <User size={12} />
        </span>
      ) : null}
    </div>
  );
}

/**
 * Feedback lives inside Step Inspection. It is intentionally compact: the
 * composer, conversation turns for this step, and the agent's revised
 * interpretation and operations. Feedback is sent to the runtime; nothing
 * here fabricates a revision.
 */
export function ConversationPanel({
  view,
  step,
  busy,
  standalone = false,
  onFeedback,
  onAccept,
  onReject,
  onReplay,
}: {
  view: RunView;
  step: StepView;
  busy: boolean;
  standalone?: boolean;
  onFeedback: (text: string) => Promise<boolean>;
  onAccept: (proposal: Proposal) => void;
  onReject: (proposal: Proposal) => void;
  onReplay: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  const proposalById = useMemo(
    () => new Map(step.proposals.map((proposal) => [proposal.id, proposal])),
    [step.proposals],
  );
  const renderedProposals = new Set<string>();
  const ordered: Array<
    { type: "message"; message: Message } | { type: "proposal"; proposal: Proposal }
  > = [];
  for (const message of step.messages) {
    if (message.role === "agent" && message.proposal_id && proposalById.has(message.proposal_id)) {
      if (!renderedProposals.has(message.proposal_id)) {
        renderedProposals.add(message.proposal_id);
        ordered.push({ type: "proposal", proposal: proposalById.get(message.proposal_id)! });
      }
    } else {
      ordered.push({ type: "message", message });
    }
  }
  for (const proposal of step.proposals) {
    if (!renderedProposals.has(proposal.id)) {
      renderedProposals.add(proposal.id);
      ordered.push({ type: "proposal", proposal });
    }
  }

  const feedbackEnabled = view.capabilities.feedback;
  const disabledReason =
    view.status === "running"
      ? "Pause the run before sending feedback."
      : view.status === "awaiting_approval"
        ? view.approval?.matched_breakpoint
          ? "Use Give feedback in the breakpoint gate above; the gate remains pending."
          : "Resolve the approval gate before sending feedback."
        : view.status === "failed"
          ? "This run failed. Start a new run."
          : !feedbackEnabled
            ? "Feedback is not available for this run state."
            : null;

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const ok = await onFeedback(text);
      if (ok) setDraft("");
    } finally {
      setSending(false);
    }
  };

  return (
    <section
      id="conversation"
      className={cx(
        standalone ? "card h-fit px-3.5 py-3" : "mt-4 border-t border-line/70 pt-3.5",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5">
        <div className="flex items-center gap-1.5">
          <MessageSquare size={13} className="text-accent-600" />
          <h3 className="text-[12px] font-semibold text-ink">
            {standalone ? "Revision" : "Human Feedback"}
          </h3>
        </div>
        {step.is_read_only ? (
          <span className="text-[9.5px] text-faint">
            {step.is_skipped ? "Skipped after a rejected write." : "Completed step · read-only"}
          </span>
        ) : null}
      </div>
      {standalone ? (
        <p className="mt-0.5 text-[9.5px] leading-snug text-faint">
          Human feedback and the agent's proposed change, kept beside the original
          interpretation and evidence.
        </p>
      ) : null}

      {ordered.length > 0 ? (
        <div className="mt-2.5 space-y-2">
          {ordered.map((entry, index) =>
            entry.type === "message" ? (
              <MessageBubble key={entry.message.id} message={entry.message} />
            ) : (
              <ProposalCard
                key={`${entry.proposal.id}-${index}`}
                proposal={entry.proposal}
                view={view}
                step={step}
                busy={busy}
                onAccept={onAccept}
                onReject={onReject}
                onReplay={onReplay}
                onMoreFeedback={() => {
                  composerRef.current?.focus();
                  document
                    .getElementById("feedback-composer")
                    ?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              />
            ),
          )}
        </div>
      ) : null}

      <div id="feedback-composer" className="mt-2.5">
        {disabledReason ? (
          <p className="mb-1.5 flex items-center gap-1.5 text-[10px] text-muted">
            <Info size={11} className="shrink-0 text-accent-500" />
            {disabledReason}
          </p>
        ) : null}
        <div className="rounded-xl border border-line bg-white focus-within:border-accent-300 focus-within:ring-2 focus-within:ring-accent-100">
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                void submit();
              }
            }}
            rows={2}
            disabled={!feedbackEnabled || busy}
            placeholder={
              disabledReason
                ? disabledReason
                : "Ask the agent to reconsider this step — name the field, grain, or metric definition."
            }
            className="w-full resize-none rounded-t-xl bg-transparent px-3 py-2 text-[12px] leading-relaxed text-ink placeholder:text-faint focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
          <div className="flex justify-end border-t border-line/70 px-2.5 py-1.5">
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!feedbackEnabled || busy || sending || !draft.trim()}
              className="btn-primary px-2.5 py-1.5 text-[11px]"
            >
              {sending || busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              Send feedback
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
