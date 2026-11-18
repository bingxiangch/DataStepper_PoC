import { useState } from "react";
import { Info, Loader2, PauseCircle, Trash2 } from "lucide-react";
import type {
  BreakpointInterpretation,
  BreakpointOperation,
  BreakpointRule,
  RunView,
} from "../types";
import { cx } from "../lib/format";

const EXAMPLE = "Pause before DELETE or DROP operations.";

function operationLabel(operation: BreakpointOperation): string {
  return operation.toUpperCase();
}

function ruleChips(rule: BreakpointRule): { key: string; label: string }[] {
  if (rule.kind === "filter_excludes" && rule.condition) {
    return [
      { key: "filter", label: "FILTER" },
      { key: rule.condition.column, label: rule.condition.column.toUpperCase() },
      { key: rule.condition.value, label: `EXCLUDES ${rule.condition.value.toUpperCase()}` },
    ];
  }
  return rule.operation_types.map((operation) => ({
    key: operation,
    label: operationLabel(operation),
  }));
}

type Notice = string | null;

/**
 * Run-level breakpoint configuration, inline on the main page. It is a thin
 * strip rather than a dialog so it never competes with step inspection. The
 * parser, storage, and execution gate are unchanged; every action here calls
 * the same endpoints as before.
 */
export function InlineBreakpoints({
  view,
  busy,
  onInterpret,
  onActivate,
  onRemove,
  onSetActive,
}: {
  view: RunView;
  busy: boolean;
  onInterpret: (text: string) => Promise<BreakpointInterpretation>;
  onActivate: (text: string) => Promise<boolean>;
  onRemove: (breakpointId: string) => Promise<boolean>;
  onSetActive: (breakpointId: string, active: boolean) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = text.trim().length > 0 && !submitting && !busy;

  const submit = async () => {
    const value = text.trim();
    if (!value || submitting || busy) return;
    setSubmitting(true);
    setNotice(null);
    setError(null);
    try {
      const interpretation = await onInterpret(value);
      if (!interpretation.supported || !interpretation.rule) {
        setNotice(interpretation.message);
        return;
      }
      const ok = await onActivate(value);
      if (ok) {
        setText("");
        setNotice(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const toggle = async (breakpointId: string, active: boolean) => {
    if (busy || togglingId || removingId) return;
    setTogglingId(breakpointId);
    setNotice(null);
    setError(null);
    try {
      await onSetActive(breakpointId, active);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTogglingId(null);
    }
  };

  const remove = async (breakpointId: string) => {
    if (busy || removingId || togglingId) return;
    setRemovingId(breakpointId);
    setNotice(null);
    setError(null);
    try {
      await onRemove(breakpointId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <section
      className="card mb-3 px-3 py-2.5 sm:px-4"
      aria-labelledby="breakpoints-heading"
      title="Breakpoints pause before DELETE/DROP tools or filters that exclude cancelled orders. The mandatory approval gate remains in force."
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="flex shrink-0 items-center gap-1.5">
          <PauseCircle size={14} className="text-accent-600" />
          <h2 id="breakpoints-heading" className="text-[11.5px] font-semibold text-ink">
            Breakpoints
          </h2>
        </div>
        <form
          className="flex min-w-0 flex-1 basis-[280px] items-center gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="breakpoint-description" className="sr-only">
            Describe a breakpoint
          </label>
          <input
            id="breakpoint-description"
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setNotice(null);
              setError(null);
            }}
            placeholder={EXAMPLE}
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 py-1.5 text-[11px] text-ink placeholder:text-faint focus:border-accent-300 focus:outline-none focus:ring-2 focus:ring-accent-100"
          />
          <button
            type="submit"
            disabled={!canSubmit}
            className="btn-secondary shrink-0 px-2.5 py-1.5 text-[10.5px]"
          >
            {submitting ? <Loader2 size={12} className="animate-spin" /> : <PauseCircle size={12} />}
            Set Breakpoint
          </button>
        </form>
      </div>

      {notice ? (
        <p className="mt-1.5 flex items-start gap-1 text-[10px] leading-snug text-amber-800">
          <Info size={11} className="mt-0.5 shrink-0" />
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="mt-1.5 text-[10px] leading-snug text-red-700">{error}</p>
      ) : null}

      {view.breakpoints.length > 0 ? (
        <ul className="mt-1.5 space-y-0.5 border-t border-line/60 pt-1.5">
          {view.breakpoints.map((rule) => (
            <li
              key={rule.id}
              className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10.5px]"
            >
              <span
                className={cx(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  rule.active ? "bg-emerald-500" : "bg-slate-300",
                )}
              />
              <span
                className={cx(
                  "font-medium",
                  rule.active ? "text-ink" : "text-muted line-through decoration-slate-400",
                )}
              >
                {rule.label}
              </span>
              {ruleChips(rule).map((chip) => (
                <span
                  key={chip.key}
                  className={cx(
                    "chip py-0 font-mono text-[8.5px]",
                    rule.active
                      ? "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-line bg-white text-faint",
                  )}
                >
                  {chip.label}
                </span>
              ))}
              <span className="text-[9px] text-faint">before execution</span>
              {!rule.active ? (
                <span className="text-[9px] text-muted">disabled</span>
              ) : null}
              <span className="ml-auto flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => void toggle(rule.id, !rule.active)}
                  disabled={busy || togglingId !== null || removingId !== null}
                  className="btn-ghost px-1.5 py-0.5 text-[9.5px] text-muted hover:text-ink"
                  title={
                    rule.active
                      ? "Disable this rule. The rule is kept but no longer pauses execution."
                      : "Re-enable this rule."
                  }
                >
                  {togglingId === rule.id ? (
                    <Loader2 size={10} className="animate-spin" />
                  ) : null}
                  {rule.active ? "Disable" : "Enable"}
                </button>
                <button
                  type="button"
                  onClick={() => void remove(rule.id)}
                  disabled={busy || removingId !== null || togglingId !== null}
                  className="btn-ghost p-1 text-muted hover:text-red-700"
                  title="Remove this breakpoint"
                  aria-label={`Remove breakpoint ${rule.label}`}
                >
                  {removingId === rule.id ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <Trash2 size={11} />
                  )}
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[9.5px] leading-snug text-faint">
          No breakpoint set. Try “Pause before DELETE or DROP operations.” or “Pause if the
          agent excludes cancelled orders from the analysis.” Destructive writes still require
          mandatory approval.
        </p>
      )}
    </section>
  );
}
