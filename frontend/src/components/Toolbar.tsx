import type { ReactNode } from "react";
import {
  ArrowLeft,
  GitCompare,
  LogIn,
  LogOut,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
} from "lucide-react";
import type { RunView } from "../types";
import { shortId, statusTone } from "../lib/format";
import { Badge } from "./ui";

type Command = "step-into" | "step-over" | "step-out" | "continue" | "pause";

export function Toolbar({
  view,
  busy,
  onCommand,
  onReplay,
  onCompare,
  onOpenParent,
}: {
  view: RunView;
  busy: boolean;
  onCommand: (command: Command) => void;
  onReplay: () => void;
  onCompare: () => void;
  onOpenParent?: () => void;
}) {
  const tone = statusTone(view.status);
  const caps = view.capabilities;
  const button = (
    label: string,
    icon: ReactNode,
    enabled: boolean,
    action: () => void,
    title: string,
    active = false,
  ) => (
    <button
      type="button"
      onClick={action}
      disabled={!enabled || busy}
      title={title}
      className={
        active
          ? "btn-primary px-3 py-1.5 text-xs"
          : "btn-secondary px-3 py-1.5 text-xs"
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );

  return (
    <div className="card flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 sm:px-4">
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-mono text-xs font-semibold text-ink">{shortId(view.id)}</span>
        <span className={`chip ${tone.className}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
          {view.status_label}
        </span>
        {view.parent_run_id ? (
          <button
            type="button"
            onClick={onOpenParent}
            className="hidden items-center gap-1 text-[11px] text-accent-700 hover:underline sm:inline-flex"
            title={`Open original run ${view.parent_run_id}`}
          >
            <ArrowLeft size={12} />
            original
          </button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {button(
          "Step Into",
          <LogIn size={14} />,
          caps.step_into,
          () => onCommand("step-into"),
          "Enter the current operation context without executing. Once inside, Step Into executes one operation.",
        )}
        {button(
          "Step Over",
          <SkipForward size={14} />,
          caps.step_over,
          () => onCommand("step-over"),
          "Execute the entire current workflow step.",
        )}
        {button(
          "Step Out",
          <LogOut size={14} />,
          caps.step_out,
          () => onCommand("step-out"),
          "Execute the remaining operations in the current step and leave it.",
        )}
        {button(
          "Continue",
          <Play size={14} />,
          caps.continue,
          () => onCommand("continue"),
          "Run until completion, a pause request, or a mandatory approval boundary.",
        )}
        {button(
          "Pause",
          <Pause size={14} />,
          caps.pause,
          () => onCommand("pause"),
          "Request a safe pause at the next operation boundary.",
        )}
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        {view.approval ? (
          <Badge tone="warning" className="mr-1">
            {view.approval.matched_breakpoint
              ? view.approval.mandatory
                ? "Breakpoint · approval"
                : "Breakpoint hit"
              : "Approval pending"}
          </Badge>
        ) : null}
        <button
          type="button"
          onClick={onReplay}
          disabled={!caps.replay || busy}
          className="btn-secondary px-3 py-1.5 text-xs"
          title={
            caps.replay
              ? "Re-execute from the checkpoint before the revised step and compare."
              : "Replay unlocks after accepting a revision that affects already-executed work."
          }
        >
          <RotateCcw size={14} />
          Replay
        </button>
        <button
          type="button"
          onClick={onCompare}
          disabled={!caps.compare || busy}
          className="btn-secondary px-3 py-1.5 text-xs"
          title={caps.compare ? "Compare original and replay runs." : "No replay branch yet."}
        >
          <GitCompare size={14} />
          Compare
        </button>
      </div>
    </div>
  );
}
