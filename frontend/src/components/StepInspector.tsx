import { useState } from "react";
import {
  BarChart3,
  BookOpen,
  ChevronRight,
  Lock,
  Workflow,
} from "lucide-react";
import type {
  EvidenceItem,
  Operation,
  Proposal,
  Report,
  RunView,
  SemanticBinding,
  StepView,
} from "../types";
import { cx, pretty, statusTone } from "../lib/format";
import {
  describeAction,
  groupPlanOperations,
} from "../lib/stepPresentation";
import {
  dataEffectsForStep,
  type DataEffectBlock,
  type EffectMetric,
  type EffectTone,
} from "../lib/dataEffects";
import { Badge, RichText, SectionHeading } from "./ui";
import { ConversationPanel } from "./ConversationPanel";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function intText(value: unknown): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : String(value);
}

function normalized(value: string): string {
  return value.toLowerCase().replace(/[-_]/g, " ");
}

function semanticChoices(view: RunView, step: StepView): SemanticBinding[] {
  const context = [
    step.title,
    step.goal,
    step.understanding,
    step.rationale,
    ...step.target_data,
    ...step.operations.map((op) => pretty(op.args)),
  ].join(" ");
  const compact = normalized(context);
  return view.task_spec.semantic_bindings.filter((binding) => {
    const concept = normalized(binding.concept);
    const conceptWords = concept.split(" ").filter((word) => word.length > 3);
    const qualified = `${binding.source_table}.${binding.column}`.toLowerCase();
    const sourceMentioned = compact.includes(normalized(binding.source_table));
    return (
      compact.includes(concept) ||
      context.toLowerCase().includes(qualified) ||
      context.toLowerCase().includes(binding.column.toLowerCase()) ||
      (sourceMentioned && conceptWords.some((word) => compact.includes(word)))
    );
  });
}

function keyBinding(view: RunView, step: StepView): SemanticBinding | null {
  return semanticChoices(view, step)[0] ?? null;
}

interface UpstreamEffect {
  stepNumber: number;
  text: string;
}

/**
 * One observed, read-only consequence of an earlier join whose right table is
 * the table this step's semantic binding names. It is displayed as upstream
 * context only; nothing here is an operation of the inspected step.
 */
function upstreamMeasuredEffect(
  view: RunView,
  step: StepView,
  binding: SemanticBinding | null,
): UpstreamEffect | null {
  if (!binding) return null;
  const index = view.steps.findIndex((candidate) => candidate.id === step.id);
  for (let i = index - 1; i >= 0; i -= 1) {
    const upstream = view.steps[i];
    for (const operation of upstream.operations) {
      for (const evidence of operation.evidence) {
        if (evidence.status !== "OBSERVED" || evidence.kind !== "join") continue;
        const details = asRecord(evidence.details);
        if (String(details.right ?? "") !== binding.source_table) continue;
        const repeated = Number(details.payment_records_repeated);
        if (!Number.isFinite(repeated) || repeated <= 0) continue;
        const max = Number(details.max_copies_per_payment_record);
        const left = String(details.left ?? "");
        const right = String(details.right ?? "");
        return {
          stepNumber: upstream.number,
          text: `${intText(repeated)} payment records were counted more than once after joining ${left} to ${right}${
            Number.isFinite(max) ? ` (up to ${intText(max)} copies per record)` : ""
          }.`,
        };
      }
    }
  }
  return null;
}

function TechnicalDetails({
  operations,
  previewEvidence,
  report,
}: {
  operations: Operation[];
  previewEvidence: EvidenceItem[];
  report: Report | null;
}) {
  return (
    <details className="group mt-2">
      <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-[10.5px] font-medium text-muted transition hover:text-accent-700">
        <ChevronRight
          size={11}
          className="transition-transform group-open:rotate-90"
        />
        Show technical details
      </summary>
      <div className="mt-2 space-y-3">
        {operations.map((operation) => {
          const previews = previewEvidence.filter(
            (evidence) => evidence.operation_id === operation.id,
          );
          const raw = {
            tool: operation.tool,
            status: operation.status,
            requires_approval: operation.requires_approval || undefined,
            arguments: operation.args,
            error: operation.error || undefined,
            preview_evidence: previews.map((evidence) => ({
              kind: evidence.kind,
              summary: evidence.summary,
              facts: evidence.facts,
              details: evidence.details,
            })),
            observed_evidence: operation.evidence
              .filter((evidence) => evidence.status === "OBSERVED")
              .map((evidence) => ({
                kind: evidence.kind,
                summary: evidence.summary,
                facts: evidence.facts,
                details: evidence.details,
              })),
            ...(operation.tool === "compose_report" && report
              ? {
                  report: {
                    summary: report.summary,
                    notes: report.notes,
                    metrics: report.metrics,
                  },
                }
              : {}),
          };
          return (
            <div key={operation.id}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-mono text-[10px] font-semibold text-ink">
                  {operation.tool}
                </span>
                <span className="text-[9px] uppercase tracking-wide text-faint">
                  {operation.status.replace(/_/g, " ")}
                </span>
                {operation.requires_approval ? (
                  <span className="text-[9px] text-amber-700">approval required</span>
                ) : null}
              </div>
              <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-[#FAF9FD] p-2.5 font-mono text-[9.5px] leading-relaxed text-muted">
                {pretty(raw)}
              </pre>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function InterpretationSection({ view, step }: { view: RunView; step: StepView }) {
  const binding = keyBinding(view, step);
  const upstream = upstreamMeasuredEffect(view, step, binding);
  const interpretation = (step.understanding || step.goal || "").replace(/\s+/g, " ").trim();
  return (
    <section aria-labelledby="interpretation-heading">
      <SectionHeading
        icon={<BookOpen size={15} />}
        title="Explicit Interpretation"
      />
      <p className="mt-1.5 max-w-[70ch] text-[13px] leading-relaxed text-ink/90">
        <RichText text={interpretation || "No interpretation was recorded for this step."} />
      </p>
      {binding ? (
        <p className="mt-1 text-[11.5px] leading-relaxed text-muted">
          <span className="font-medium text-ink">Current assumption:</span>{" "}
          <RichText text={binding.concept} />
          <span className="mx-1 text-faint">→</span>
          <code className="font-mono text-[11px] font-semibold text-ink">
            {binding.source_table}.{binding.column}
          </code>
        </p>
      ) : null}
      {upstream ? (
        <p
          className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-[10.5px] leading-relaxed text-muted"
          title="Observed during an earlier step. This reference is read-only and was not executed in this step."
        >
          <span className="chip shrink-0 border-slate-200 bg-slate-50 py-0 text-[8.5px] font-semibold uppercase tracking-wide text-slate-600">
            Upstream effect · Step {upstream.stepNumber}
          </span>
          <span>{upstream.text}</span>
        </p>
      ) : null}
    </section>
  );
}

function OperationsSection({ step }: { step: StepView }) {
  const executed = step.operations.filter((operation) =>
    ["completed", "failed", "rejected"].includes(operation.status),
  ).length;
  const state =
    step.operations.length === 0
      ? null
      : executed === step.operations.length
        ? { label: "Executed", tone: "observed" as const }
        : executed === 0
          ? { label: "Proposed", tone: "preview" as const }
          : { label: "In progress", tone: "preview" as const };
  const actions = groupPlanOperations(step.operations);

  return (
    <section aria-labelledby="operations-heading">
      <SectionHeading
        icon={<Workflow size={15} />}
        title="Operations"
        right={
          state ? (
            <Badge tone={state.tone} className="py-0 text-[9px]">
              {state.label}
            </Badge>
          ) : null
        }
      />
      {step.operations.length === 0 ? (
        <p className="mt-2 text-[12px] leading-relaxed text-muted">
          No tool operations. This step only makes the interpretation explicit.
        </p>
      ) : (
        <ol className="mt-1.5 list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-ink marker:text-[11px] marker:font-semibold marker:text-faint">
          {actions.map((action, index) => {
            const errors = action.operations
              .map((operation) => operation.error)
              .filter((error): error is string => Boolean(error));
            return (
              <li key={`${action.key}-${index}`}>
                <span>
                  <RichText text={describeAction(action)} />
                </span>
                {errors.map((error) => (
                  <span
                    key={error}
                    className="mt-0.5 block text-[11px] leading-snug text-red-600"
                  >
                    {error}
                  </span>
                ))}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function metricValueClass(tone: EffectTone | undefined): string {
  if (tone === "warning") return "text-amber-950";
  if (tone === "muted") return "text-slate-600";
  return "text-ink";
}

function noteClass(tone: EffectTone): string {
  if (tone === "warning") return "text-amber-900";
  if (tone === "muted") return "text-slate-600";
  return "text-muted";
}

function EffectPair({ item }: { item: EffectMetric }) {
  return (
    <span className="inline-flex min-w-0 items-baseline gap-1">
      <span
        className={cx(
          "whitespace-nowrap text-[14px] font-semibold leading-tight tabular-nums tracking-[-0.01em]",
          metricValueClass(item.tone),
        )}
      >
        {item.value}
      </span>
      <span className="whitespace-nowrap text-[9px] font-semibold uppercase leading-tight tracking-[0.07em] text-faint">
        {item.label}
      </span>
      {item.hint ? (
        <span className="min-w-0 text-[9.5px] leading-tight text-muted">{item.hint}</span>
      ) : null}
    </span>
  );
}

function DataEffectRow({ block, first }: { block: DataEffectBlock; first: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const extra = block.metrics.filter((item) => item.detail);
  const visible = expanded ? block.metrics : block.metrics.filter((item) => !item.detail);
  return (
    <div className={cx("py-1.5", !first && "border-t border-line/60")}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="inline-flex min-w-0 items-baseline gap-1.5">
          {block.tone === "warning" ? (
            <span className="h-1.5 w-1.5 shrink-0 self-center rounded-full bg-amber-400" />
          ) : null}
          <span className="shrink-0 text-[8.5px] font-semibold uppercase tracking-[0.09em] text-faint">
            {block.kicker}
          </span>
          <span className="min-w-0 break-words text-[10.5px] font-semibold text-ink/85">
            {block.title}
          </span>
        </span>
        <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-3 gap-y-1">
          {visible.map((item, index) => (
            <EffectPair key={`${item.label}-${index}`} item={item} />
          ))}
          {extra.length > 0 ? (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              className="text-[9.5px] font-medium text-accent-700 transition hover:underline"
            >
              {expanded ? "hide extra" : `+${extra.length} more`}
            </button>
          ) : null}
        </span>
      </div>
      {block.note ? (
        <p className={cx("mt-0.5 text-[10.5px] leading-snug", noteClass(block.tone))}>
          <RichText text={block.note} />
        </p>
      ) : null}
    </div>
  );
}

function DataEffectsSection({ view, step }: { view: RunView; step: StepView }) {
  const blocks = dataEffectsForStep(step, view);
  const observed = step.operations.some((operation) =>
    operation.evidence.some((evidence) => evidence.status === "OBSERVED"),
  );
  return (
    <section aria-labelledby="data-effects-heading">
      <SectionHeading
        icon={<BarChart3 size={15} />}
        title="Data Effects"
        hint="computed from executed results"
      />
      {blocks.length === 0 ? (
        <p className="mt-1.5 rounded-lg border border-dashed border-line bg-[#FBFAFD] px-3 py-1.5 text-[11px] leading-relaxed text-muted">
          {step.operations.length === 0
            ? "This step has no data operations."
            : observed
              ? "These operations produced no numerical data effects."
              : "No executed data effects yet. Previews stay separate until the operations run."}
        </p>
      ) : (
        <div className="mt-1.5 rounded-xl border border-line/70 bg-[#FBFAFD] px-3 py-1">
          {blocks.map((block, index) => (
            <DataEffectRow key={block.key} block={block} first={index === 0} />
          ))}
        </div>
      )}
    </section>
  );
}

export function StepInspector({
  view,
  step,
  busy,
  showConversation = true,
  onJumpToCurrent,
  onFeedback,
  onAccept,
  onReject,
  onReplay,
}: {
  view: RunView;
  step: StepView;
  busy: boolean;
  showConversation?: boolean;
  onJumpToCurrent: () => void;
  onFeedback: (text: string) => Promise<boolean>;
  onAccept: (proposal: Proposal) => void;
  onReject: (proposal: Proposal) => void;
  onReplay: () => void;
}) {
  const current = view.steps[view.cursor.step_index];
  const tone = statusTone(step.status);
  return (
    <section className="card overflow-hidden">
      <div className="border-b border-line/70 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="section-label">
              Step {step.number} of {view.steps.length}
            </div>
            <h2 className="mt-0.5 text-lg font-bold tracking-[-0.02em] text-ink sm:text-xl">
              {step.title}
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cx("chip py-0.5 text-[10px]", tone.className)}>
              <span className={cx("h-1.5 w-1.5 rounded-full", tone.dot)} />
              {tone.label}
            </span>
            {step.is_read_only ? (
              <Badge className="py-0.5 text-[9.5px]">
                <Lock size={9} /> {step.is_skipped ? "skipped" : "read-only"}
              </Badge>
            ) : null}
          </div>
        </div>
        {!step.is_current ? (
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-1.5 text-[10px] text-muted">
            <span className="flex items-center gap-1">
              <Lock size={10} className="text-faint" />
              {view.status === "completed"
                ? "Run completed. This is a historical, read-only view."
                : current
                  ? `Historical, read-only view. Execution is paused at step ${current.number}.`
                  : "Historical, read-only view."}
            </span>
            {current ? (
              <button
                type="button"
                onClick={onJumpToCurrent}
                className="font-medium text-accent-700 hover:underline"
              >
                Jump to current step
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="px-4 py-3 sm:px-5">
        <InterpretationSection view={view} step={step} />
        <div className="mt-3">
          <OperationsSection step={step} />
        </div>
        <div className="mt-3">
          <DataEffectsSection view={view} step={step} />
        </div>
        {step.operations.length > 0 ? (
          <TechnicalDetails
            key={step.id}
            operations={step.operations}
            previewEvidence={step.preview_evidence}
            report={view.report}
          />
        ) : null}
        {showConversation ? (
          <ConversationPanel
            view={view}
            step={step}
            busy={busy}
            onFeedback={onFeedback}
            onAccept={onAccept}
            onReject={onReject}
            onReplay={onReplay}
          />
        ) : null}
      </div>
    </section>
  );
}
