import type {
  EvidenceItem,
  Operation,
  RunView,
  StepView,
} from "../types";
import { humanMetricName } from "./stepPresentation";

export type EffectTone = "normal" | "warning" | "muted";

export interface EffectMetric {
  label: string;
  value: string;
  hint?: string;
  tone?: EffectTone;
  /** Secondary statistic, shown only when the row is expanded. */
  detail?: boolean;
}

export interface DataEffectBlock {
  key: string;
  kicker: string;
  title: string;
  metrics: EffectMetric[];
  note?: string;
  tone: EffectTone;
}

interface EffectDraft {
  kicker: string;
  title: string;
  metrics: EffectMetric[];
  note?: string;
  tone?: EffectTone;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function intText(value: number): string {
  return value.toLocaleString("en-US");
}

function factValue(evidence: EvidenceItem, label: string): string | null {
  return evidence.facts.find((fact) => fact.label === label)?.value ?? null;
}

function labelParts(evidence: EvidenceItem): { kicker: string; title: string } {
  const [head, ...rest] = evidence.label.split("·");
  const kicker = head.trim();
  const title = rest.join("·").trim();
  return { kicker, title: title || kicker };
}

function tableTarget(evidence: EvidenceItem): string {
  const details = asRecord(evidence.details);
  const target = details.target_table;
  if (typeof target === "string" && target) return target;
  const fact = factValue(evidence, "Target");
  return fact ? fact.split(" ")[0] : labelParts(evidence).title;
}

function metric(label: string, value: string, tone?: EffectTone): EffectMetric {
  return { label, value, tone };
}

/** A contextual statistic; hidden until the operator expands the effect row. */
function secondary(label: string, value: string, tone?: EffectTone): EffectMetric {
  return { label, value, tone, detail: true };
}

function filterEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const input = toNumber(details.input_rows);
  const retained = toNumber(details.output_rows);
  const removed = toNumber(details.removed_rows);
  const metrics: EffectMetric[] = [];
  if (input !== null) metrics.push(secondary("Input rows", intText(input)));
  if (retained !== null) metrics.push(metric("Retained", intText(retained)));
  if (removed !== null) {
    metrics.push(metric("Removed", intText(removed), removed === 0 ? "muted" : "normal"));
  }
  if (metrics.length === 0) return null;
  return {
    ...labelParts(evidence),
    metrics,
    note: removed === 0 ? "The filter retained every input row." : undefined,
  };
}

function joinEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const output = toNumber(details.output_rows);
  const repeated = toNumber(details.payment_records_repeated);
  const maxCopies = toNumber(details.max_copies_per_payment_record);
  const paymentRecords = toNumber(details.payment_records_after_join);
  const ratio = toNumber(details.payment_value_inflation_ratio);

  if (repeated !== null || maxCopies !== null || paymentRecords !== null) {
    const metrics: EffectMetric[] = [];
    if (repeated !== null) {
      metrics.push({
        label: "Repeated payment records",
        value: intText(repeated),
        hint: paymentRecords !== null ? `of ${intText(paymentRecords)} records` : "appear more than once",
        tone: repeated > 0 ? "warning" : "normal",
      });
    }
    if (maxCopies !== null) {
      metrics.push({
        label: "Max copies per record",
        value: intText(maxCopies),
        hint: "across joined rows",
        tone: maxCopies > 1 ? "warning" : "normal",
      });
    }
    if (output !== null) metrics.push(secondary("Joined rows", intText(output)));
    const notes: string[] = [];
    if (repeated !== null && repeated > 0) {
      notes.push(
        "Repeated records appear in multiple joined rows, so measures summed from them can be overcounted.",
      );
      if (ratio !== null && ratio > 1) {
        notes.push(`The joined value sum is ${ratio.toFixed(2)}× the distinct-record sum.`);
      }
    } else if (repeated === 0) {
      notes.push("No payment record was repeated across joined rows.");
    }
    return {
      ...labelParts(evidence),
      metrics,
      note: notes.join(" ") || undefined,
      tone: repeated !== null && repeated > 0 ? "warning" : "normal",
    };
  }

  const left = toNumber(details.left_rows);
  const right = toNumber(details.right_rows);
  const repeatedKeys = toNumber(details.right_keys_with_multiple_records);
  const maxKey = toNumber(details.max_right_records_per_key);
  const metrics: EffectMetric[] = [];
  if (left !== null) metrics.push(secondary("Left rows", intText(left)));
  if (right !== null) metrics.push(secondary("Right rows", intText(right)));
  if (output !== null) metrics.push(metric("Joined rows", intText(output)));
  if (repeatedKeys !== null) {
    metrics.push(
      metric(
        "Repeated right keys",
        intText(repeatedKeys),
        repeatedKeys > 0 ? "warning" : "normal",
      ),
    );
  }
  if (metrics.length === 0) return null;
  const note =
    repeatedKeys !== null && repeatedKeys > 0 && maxKey !== null
      ? `${intText(repeatedKeys)} right-side keys have more than one record (up to ${intText(
          maxKey,
        )} each), so matching left rows are repeated and measures can be overcounted.`
      : undefined;
  return {
    ...labelParts(evidence),
    metrics,
    note,
    tone: repeatedKeys !== null && repeatedKeys > 0 ? "warning" : "normal",
  };
}

function groupEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const input = toNumber(details.input_rows);
  const groups = toNumber(details.output_groups);
  const largest = toNumber(details.largest_group_rows);
  const multi = toNumber(details.groups_with_multiple_rows);
  const keys = Array.isArray(details.group_keys)
    ? details.group_keys.map((key) => String(key))
    : [];
  const identity = keys.some((key) => ["customer_id", "customer_unique_id"].includes(key));

  const metrics: EffectMetric[] = [];
  if (input !== null) metrics.push(secondary("Input rows", intText(input)));
  if (groups !== null) metrics.push(metric("Groups", intText(groups)));
  if (largest !== null) {
    metrics.push(
      secondary("Largest group", `${intText(largest)} ${largest === 1 ? "row" : "rows"}`),
    );
  }
  if (multi !== null) {
    metrics.push({
      label: "Multi-row groups",
      value: intText(multi),
      hint: "groups with ≥2 rows",
    });
  }
  if (metrics.length === 0) return null;
  const identityNote =
    identity && groups !== null && multi !== null && groups > 0
      ? multi > 0
        ? `${intText(multi)} of ${intText(
            groups,
          )} groups contain at least two rows; per-order events are not distinguishable at this grain.`
        : `All ${intText(
            groups,
          )} groups contain exactly one row, so this identity yields no repeat purchases; a zero rate may reflect the binding rather than customer behavior.`
      : undefined;
  return {
    ...labelParts(evidence),
    metrics,
    note: identityNote,
    tone: identityNote ? "warning" : "normal",
  };
}

function metricEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const name = labelParts(evidence).title;
  const displayed =
    factValue(evidence, "Metric value") ??
    (details.value !== null && details.value !== undefined ? String(details.value) : null);
  if (!displayed) return null;

  const metrics: EffectMetric[] = [metric(humanMetricName(name), displayed)];
  const numerator = toNumber(details.numerator);
  const denominator = toNumber(details.denominator);
  if (details.kind === "share_of_rows" && numerator !== null && denominator !== null) {
    metrics.push(secondary("Numerator", intText(numerator)));
    metrics.push(secondary("Denominator", intText(denominator)));
  } else {
    const sourceRows = factValue(evidence, "Source rows");
    if (sourceRows) metrics.push(secondary("Source rows", sourceRows));
  }
  return { ...labelParts(evidence), metrics };
}

function profileEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const rows = toNumber(details.rows);
  const distinct = toNumber(details.unique);
  const nulls = toNumber(details.nulls);
  if (rows === null || distinct === null || nulls === null) return null;
  return {
    ...labelParts(evidence),
    metrics: [
      secondary("Rows", intText(rows)),
      metric("Distinct", intText(distinct)),
      metric("Nulls", intText(nulls), nulls > 0 ? "warning" : "normal"),
    ],
    note:
      distinct === rows && nulls === 0
        ? "The column holds a distinct value on every row."
        : undefined,
  };
}

function schemaEffect(evidence: EvidenceItem): EffectDraft | null {
  const rows = factValue(evidence, "Rows");
  const columns = factValue(evidence, "Columns inspected");
  const metrics: EffectMetric[] = [];
  if (rows) metrics.push(metric("Table rows", rows));
  if (columns) metrics.push(metric("Columns", columns));
  if (metrics.length === 0) return null;
  const uniqueFacts = evidence.facts.filter((fact) => /unique/i.test(fact.label));
  return {
    ...labelParts(evidence),
    metrics,
    note:
      uniqueFacts.length > 0
        ? uniqueFacts
            .slice(0, 3)
            .map((fact) => `${fact.label} ${fact.value}`)
            .join(" · ")
        : undefined,
  };
}

function sampleEffect(evidence: EvidenceItem): EffectDraft | null {
  const returned = factValue(evidence, "Returned rows");
  if (!returned) return null;
  return {
    ...labelParts(evidence),
    metrics: [metric("Returned rows", returned)],
  };
}

function sandboxEffect(evidence: EvidenceItem): EffectDraft | null {
  const rows = factValue(evidence, "Rows");
  const canceled = factValue(evidence, "Canceled");
  if (!rows && !canceled) return null;
  return {
    ...labelParts(evidence),
    metrics: [
      ...(rows ? [metric("Rows", rows)] : []),
      ...(canceled ? [metric("Canceled", canceled)] : []),
    ],
  };
}

function sandboxWriteEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const deleted = toNumber(details.deleted_rows);
  const rowsAfter = toNumber(details.rows_after);
  const customersAfter = toNumber(details.customers_after);
  if (deleted === null && rowsAfter === null && customersAfter === null) return null;
  const metrics: EffectMetric[] = [];
  if (deleted !== null) {
    metrics.push(metric("Deleted rows", intText(deleted), deleted > 0 ? "warning" : "muted"));
  }
  if (customersAfter !== null) {
    metrics.push(secondary("Customers after", intText(customersAfter)));
  } else if (rowsAfter !== null) {
    metrics.push(secondary("Rows after", intText(rowsAfter)));
  }
  return { ...labelParts(evidence), title: tableTarget(evidence), metrics };
}

function customerPreviewEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const matching = toNumber(details.matching_customers);
  const total = toNumber(details.customers_total);
  const neverOrdered = toNumber(details.never_ordered);
  if (matching === null && total === null && neverOrdered === null) return null;
  const metrics: EffectMetric[] = [];
  if (matching !== null) metrics.push(metric("Matching rows", intText(matching)));
  if (total !== null) metrics.push(metric("Customers total", intText(total)));
  if (neverOrdered !== null) metrics.push(metric("Never ordered", intText(neverOrdered)));
  const reference =
    typeof details.declared_reference_date === "string" ? details.declared_reference_date : null;
  const cutoff = typeof details.cutoff === "string" ? details.cutoff : null;
  return {
    ...labelParts(evidence),
    title: tableTarget(evidence),
    metrics,
    note:
      reference || cutoff
        ? `Reference ${reference ?? "—"}; cutoff ${cutoff ?? "—"}.`
        : undefined,
  };
}

function archiveEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const archivedNow = toNumber(details.archived_now);
  const archivedTotal = toNumber(details.archived_total);
  const removed = toNumber(details.rows_removed);
  const customersAfter = toNumber(details.customers_after);
  const metrics: EffectMetric[] = [];
  if (archivedNow !== null) metrics.push(metric("Archived now", intText(archivedNow)));
  if (archivedTotal !== null) {
    metrics.push(secondary("Archived total", intText(archivedTotal)));
  }
  if (removed !== null) {
    metrics.push(metric("Rows removed", intText(removed), removed > 0 ? "warning" : "muted"));
  }
  if (customersAfter !== null) {
    metrics.push(secondary("Customers after", intText(customersAfter)));
  }
  if (metrics.length === 0) return null;
  return {
    ...labelParts(evidence),
    title: tableTarget(evidence),
    metrics,
    note:
      removed === 0
        ? "Archived rows remain in the disposable copy; no customer records were removed."
        : undefined,
  };
}

function auditEffect(evidence: EvidenceItem): EffectDraft | null {
  const details = asRecord(evidence.details);
  const source = toNumber(details.source_customer_rows);
  const sandbox = toNumber(details.sandbox_customer_rows);
  const removed = toNumber(details.rows_removed);
  const archived = toNumber(details.archived_rows);
  const active = toNumber(details.active_rows);
  const metrics: EffectMetric[] = [];
  if (source !== null) metrics.push(secondary("Source rows", intText(source)));
  if (sandbox !== null) metrics.push(secondary("Sandbox rows", intText(sandbox)));
  if (removed !== null) {
    metrics.push(metric("Rows removed", intText(removed), removed > 0 ? "warning" : "muted"));
  }
  if (archived !== null) metrics.push(metric("Archived", intText(archived)));
  if (active !== null) metrics.push(metric("Active", intText(active)));
  if (metrics.length === 0) return null;
  return { ...labelParts(evidence), title: tableTarget(evidence), metrics };
}

function rejectedEffect(evidence: EvidenceItem): EffectDraft {
  const details = asRecord(evidence.details);
  const reason = typeof details.reason === "string" ? details.reason.replace(/\.+$/, "").trim() : "";
  return {
    ...labelParts(evidence),
    title: "Destructive write rejected",
    metrics: [metric("Rows changed", "0", "muted")],
    note: reason ? `Reason: ${reason}.` : "No rows were changed.",
    tone: "muted",
  };
}

function reportEffect(evidence: EvidenceItem, view: RunView): EffectDraft | null {
  const report = view.report;
  const metrics: EffectMetric[] = report
    ? report.metrics.map((item) => metric(humanMetricName(item.name), item.formatted))
    : evidence.facts.map((fact) => metric(humanMetricName(fact.label), fact.value));
  if (metrics.length === 0 && !report?.summary) return null;

  const decision = view.approval_history[view.approval_history.length - 1];
  let note: string | undefined;
  let tone: EffectTone = "normal";
  if (decision?.status === "rejected") {
    const reason = (decision.reason || "not recorded").replace(/\.+$/, "").trim();
    note = `Destructive operation rejected; no rows were changed. Reason: ${reason}.`;
    tone = "muted";
  } else if (decision?.status === "approved") {
    note = "Destructive operation approved and executed exactly once.";
    tone = "muted";
  } else {
    const check = view.reference_check;
    if (check && !check.error && check.workflow_formatted && check.reference_formatted) {
      note = `Independent reference ${check.reference_formatted} vs reported ${check.workflow_formatted} — ${
        check.agreement ? "agrees" : "disagrees"
      }.`;
      tone = check.agreement ? "normal" : "warning";
      metrics.push({
        label: "Independent reference",
        value: check.reference_formatted,
        hint: `reported ${check.workflow_formatted}`,
        tone: check.agreement ? "normal" : "warning",
      });
    } else if (report?.summary) {
      note = report.summary;
    }
  }

  return {
    kicker: "REPORT",
    title: report?.title || labelParts(evidence).title || "Final report",
    metrics,
    note,
    tone,
  };
}

function factsFallback(evidence: EvidenceItem): EffectDraft | null {
  const facts = evidence.facts.filter((fact) => /\d/.test(fact.value)).slice(0, 4);
  const metrics = facts.map((fact, index) =>
    index < 2 ? metric(fact.label, fact.value) : secondary(fact.label, fact.value),
  );
  if (metrics.length === 0) return null;
  return { ...labelParts(evidence), metrics };
}

function draftForEvidence(
  evidence: EvidenceItem,
  operation: Operation,
  view: RunView,
): EffectDraft | null {
  switch (evidence.kind) {
    case "filter":
      return filterEffect(evidence);
    case "join":
      return joinEffect(evidence);
    case "groupby":
      return groupEffect(evidence);
    case "metric":
      return metricEffect(evidence);
    case "profile":
      return profileEffect(evidence);
    case "schema":
      return schemaEffect(evidence);
    case "sample":
      return sampleEffect(evidence);
    case "sandbox":
      return sandboxEffect(evidence);
    case "sandbox_write":
      return sandboxWriteEffect(evidence);
    case "sandbox_customer_preview":
      return customerPreviewEffect(evidence);
    case "sandbox_archive":
      return archiveEffect(evidence);
    case "sandbox_audit":
      return auditEffect(evidence);
    case "approval_rejected":
      return rejectedEffect(evidence);
    case "report":
      return reportEffect(evidence, view);
    case "data_dictionary":
      return null;
    default:
      return operation.tool === "compose_report" ? reportEffect(evidence, view) : factsFallback(evidence);
  }
}

function blockForOperation(operation: Operation, view: RunView): DataEffectBlock | null {
  const observed = operation.evidence.filter((evidence) => evidence.status === "OBSERVED");
  const drafts = observed
    .map((evidence) => draftForEvidence(evidence, operation, view))
    .filter((draft): draft is EffectDraft => Boolean(draft));
  if (drafts.length === 0) return null;

  const primary = drafts[0];
  const metrics = drafts.flatMap((draft) => draft.metrics);
  if (metrics.length === 0) return null;
  const visible = metrics.some((item) => !item.detail)
    ? metrics
    : metrics.map((item, index) => (index === 0 ? { ...item, detail: false } : item));
  const notes = drafts
    .map((draft) => draft.note)
    .filter((note): note is string => Boolean(note));

  return {
    key: operation.id,
    kicker: primary.kicker,
    title: primary.title,
    metrics: visible,
    note: notes.length > 0 ? notes.join(" ") : undefined,
    tone: drafts.some((draft) => draft.tone === "warning")
      ? "warning"
      : drafts.some((draft) => draft.tone === "muted")
        ? "muted"
        : "normal",
  };
}

/**
 * Numerical consequences of executed operations. Only OBSERVED evidence is
 * used; previews and proposed operations stay separate from real effects.
 */
export function dataEffectsForStep(step: StepView, view: RunView): DataEffectBlock[] {
  return step.operations
    .map((operation) => blockForOperation(operation, view))
    .filter((block): block is DataEffectBlock => Boolean(block));
}
