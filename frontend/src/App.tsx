import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import type {
  AppConfig,
  BreakpointInterpretation,
  Comparison,
  Proposal,
  RunSummary,
  RunView,
} from "./types";
import { ApiError, api } from "./lib/api";
import { cx, shortId } from "./lib/format";
import { Header } from "./components/Header";
import { Toolbar } from "./components/Toolbar";
import { WorkflowSidebar } from "./components/WorkflowSidebar";
import { StepInspector } from "./components/StepInspector";
import { ApprovalCard } from "./components/ApprovalCard";
import { NewRunDialog, Onboarding } from "./components/NewRunDialog";
import { CompareDialog } from "./components/CompareDialog";
import { InlineBreakpoints } from "./components/InlineBreakpoints";
import { ConversationPanel } from "./components/ConversationPanel";

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

function currentStepId(view: RunView): string | null {
  if (!view.steps.length) return null;
  const index = Math.min(view.cursor.step_index, view.steps.length - 1);
  return view.steps[index]?.id ?? view.steps[0].id;
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [view, setView] = useState<RunView | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [followCurrent, setFollowCurrent] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newRunOpen, setNewRunOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [approvalReason, setApprovalReason] = useState("");
  const initialised = useRef(false);

  const refreshRuns = useCallback(async () => {
    try {
      const result = await api.runs();
      setRuns(result.runs);
    } catch {
      // A run list failure must not hide a working run view.
    }
  }, []);

  const openRun = useCallback(
    async (id: string) => {
      setBusy(true);
      try {
        const next = await api.run(id);
        setView(next);
        setSelectedStepId(currentStepId(next));
        setFollowCurrent(true);
        setCompareOpen(false);
        setError(null);
        void refreshRuns();
      } catch (caught) {
        setError(messageOf(caught));
      } finally {
        setBusy(false);
      }
    },
    [refreshRuns],
  );

  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    let cancelled = false;
    (async () => {
      try {
        const liveConfig = await api.config();
        if (!cancelled) setConfig(liveConfig);
      } catch (caught) {
        if (!cancelled) setError(messageOf(caught));
      }
      try {
        const result = await api.runs();
        if (cancelled) return;
        setRuns(result.runs);
        if (result.runs.length > 0) {
          const next = await api.run(result.runs[0].id);
          if (cancelled) return;
          setView(next);
          setSelectedStepId(currentStepId(next));
        }
      } catch (caught) {
        if (!cancelled) setError(messageOf(caught));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!view) return;
    if (!followCurrent) return;
    const id = currentStepId(view);
    if (id) setSelectedStepId(id);
  }, [view, followCurrent]);

  // A replay branch stores its parent; load the comparison on open so the
  // "Open comparison" affordance still works after a page reload.
  useEffect(() => {
    const parentId = view?.parent_run_id;
    const childId = view?.id;
    if (!parentId || !childId) return;
    let cancelled = false;
    api
      .compare(parentId, childId)
      .then((result) => {
        if (!cancelled) setComparison(result);
      })
      .catch(() => {
        // The parent may still be loading; the Compare action remains available
        // from the original run.
      });
    return () => {
      cancelled = true;
    };
  }, [view?.id, view?.parent_run_id]);

  // Poll only while execution is genuinely running.
  useEffect(() => {
    if (!view || view.status !== "running") return;
    const runId = view.id;
    let cancelled = false;
    let reported = false;
    const tick = async () => {
      try {
        const next = await api.run(runId);
        if (cancelled) return;
        setView(next);
        if (next.status !== "running") void refreshRuns();
      } catch (caught) {
        if (!cancelled && !reported) {
          reported = true;
          setError(messageOf(caught));
        }
      }
    };
    const timer = window.setInterval(() => void tick(), 650);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [view?.id, view?.status, refreshRuns]);

  const selectedStep =
    view?.steps.find((step) => step.id === selectedStepId) ??
    (view ? view.steps[Math.min(view.cursor.step_index, view.steps.length - 1)] : null);

  // A pending revision gets its own column so the original interpretation and
  // observed evidence stay visible beside the proposed change.
  const pendingRevision =
    selectedStep?.active_proposal?.status === "pending" ? selectedStep.active_proposal : null;
  const revisionActive = Boolean(pendingRevision);

  const handleCommand = async (action: string) => {
    if (!view) return;
    setBusy(true);
    try {
      const next = await api.command(view.id, action);
      setView(next);
      setFollowCurrent(true);
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleFeedback = async (text: string): Promise<boolean> => {
    if (!view || !selectedStep) return false;
    setBusy(true);
    try {
      const next = await api.feedback(view.id, selectedStep.id, text);
      setView(next);
      setFollowCurrent(false);
      setError(null);
      void refreshRuns();
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleAccept = async (proposal: Proposal) => {
    if (!view) return;
    setBusy(true);
    try {
      const next = await api.accept(view.id, proposal.id);
      setView(next);
      setFollowCurrent(false);
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async (proposal: Proposal) => {
    if (!view) return;
    setBusy(true);
    try {
      const next = await api.reject(view.id, proposal.id);
      setView(next);
      setFollowCurrent(false);
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleReplay = async () => {
    if (!view) return;
    setBusy(true);
    try {
      const result = await api.replay(view.id);
      setView(result.run);
      setComparison(result.comparison);
      setCompareOpen(true);
      setFollowCurrent(true);
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleCompare = async () => {
    if (!view) return;
    const parentId = view.parent_run_id ?? view.id;
    const childId = view.parent_run_id ? view.id : undefined;
    setBusy(true);
    try {
      const result = await api.compare(parentId, childId);
      setComparison(result);
      setCompareOpen(true);
      setError(null);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    if (!view) return;
    setBusy(true);
    try {
      const next = await api.approve(view.id);
      setView(next);
      setFollowCurrent(true);
      setApprovalReason("");
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleRejectApproval = async () => {
    if (!view) return;
    setBusy(true);
    try {
      const next = await api.rejectApproval(view.id, approvalReason.trim());
      setView(next);
      setFollowCurrent(true);
      setApprovalReason("");
      setError(null);
      void refreshRuns();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleApprovalFeedback = async (text: string): Promise<boolean> => {
    if (!view?.approval) return false;
    const stepId = view.approval.step_id;
    setBusy(true);
    try {
      const next = await api.feedback(view.id, stepId, text);
      setView(next);
      setFollowCurrent(true);
      setError(null);
      void refreshRuns();
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleInterpretBreakpoint = async (
    text: string,
  ): Promise<BreakpointInterpretation> => {
    if (!view) throw new Error("No run is open.");
    return api.interpretBreakpoint(view.id, text);
  };

  const handleActivateBreakpoint = async (text: string): Promise<boolean> => {
    if (!view) return false;
    setBusy(true);
    try {
      const next = await api.addBreakpoint(view.id, text);
      setView(next);
      setError(null);
      void refreshRuns();
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveBreakpoint = async (breakpointId: string): Promise<boolean> => {
    if (!view) return false;
    setBusy(true);
    try {
      const next = await api.removeBreakpoint(view.id, breakpointId);
      setView(next);
      setError(null);
      void refreshRuns();
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleSetBreakpointActive = async (
    breakpointId: string,
    active: boolean,
  ): Promise<boolean> => {
    if (!view) return false;
    setBusy(true);
    try {
      const next = await api.setBreakpointActive(view.id, breakpointId, active);
      setView(next);
      setError(null);
      void refreshRuns();
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = async (body: {
    task: string;
    mode: "scripted" | "live";
    dataset: string;
  }): Promise<string | null> => {
    setCreating(true);
    try {
      const next = await api.createRun(body);
      setView(next);
      setSelectedStepId(currentStepId(next));
      setFollowCurrent(true);
      setComparison(null);
      setCompareOpen(false);
      setNewRunOpen(false);
      setError(null);
      void refreshRuns();
      return null;
    } catch (caught) {
      return messageOf(caught);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="min-h-screen">
      <Header
        config={config}
        view={view}
        runs={runs}
        onNewRun={() => setNewRunOpen(true)}
        onOpenRun={(id) => void openRun(id)}
      />

      <div className="mx-auto max-w-[1280px] px-4 py-4 sm:px-6">
        {loading ? (
          <div className="grid min-h-[50vh] place-items-center">
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={17} className="animate-spin text-accent-500" />
              Loading debugger state…
            </div>
          </div>
        ) : !view ? (
          <Onboarding config={config} onNewRun={() => setNewRunOpen(true)} />
        ) : (
          <>
            <Toolbar
              view={view}
              busy={busy}
              onCommand={(command) => void handleCommand(command)}
              onReplay={() => void handleReplay()}
              onCompare={() => void handleCompare()}
              onOpenParent={
                view.parent_run_id ? () => void openRun(view.parent_run_id!) : undefined
              }
            />

            {view.error ? (
              <div className="mt-3 flex gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[11px] leading-relaxed text-red-800">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{view.error}</span>
              </div>
            ) : null}

            {view.replay_of_revision_id ? (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-accent-200 bg-accent-50/60 px-3 py-2 text-[11px] text-accent-800">
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-[10px] font-semibold">{shortId(view.parent_run_id)}</span>
                  is the immutable original. This branch re-executed from step{" "}
                  {comparison?.affected_from_step ?? "the checkpoint"}.
                </span>
                <button
                  type="button"
                  onClick={() => setCompareOpen(true)}
                  disabled={!comparison}
                  className="font-semibold hover:underline disabled:opacity-40"
                >
                  Open comparison
                </button>
              </div>
            ) : null}

            <div
              className={cx(
                "mt-3 grid gap-4",
                revisionActive
                  ? "lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[210px_minmax(0,1fr)_360px]"
                  : "lg:grid-cols-[245px_minmax(0,1fr)]",
              )}
            >
              <WorkflowSidebar
                steps={view.steps}
                selectedStepId={selectedStep?.id ?? null}
                onSelect={(id) => {
                  setSelectedStepId(id);
                  setFollowCurrent(false);
                }}
              />

              <main className="min-w-0">
                {view.approval ? (
                  <ApprovalCard
                    approval={view.approval}
                    busy={busy}
                    reason={approvalReason}
                    onReasonChange={setApprovalReason}
                    onApprove={() => void handleApprove()}
                    onReject={() => void handleRejectApproval()}
                    onFeedback={handleApprovalFeedback}
                  />
                ) : null}

                <InlineBreakpoints
                  view={view}
                  busy={busy}
                  onInterpret={handleInterpretBreakpoint}
                  onActivate={handleActivateBreakpoint}
                  onRemove={handleRemoveBreakpoint}
                  onSetActive={handleSetBreakpointActive}
                />

                {selectedStep ? (
                  <StepInspector
                    view={view}
                    step={selectedStep}
                    busy={busy}
                    showConversation={!revisionActive}
                    onFeedback={handleFeedback}
                    onAccept={(proposal) => void handleAccept(proposal)}
                    onReject={(proposal) => void handleReject(proposal)}
                    onReplay={() => void handleReplay()}
                    onJumpToCurrent={() => {
                      setFollowCurrent(true);
                      const id = currentStepId(view);
                      if (id) setSelectedStepId(id);
                    }}
                  />
                ) : null}
              </main>

              {revisionActive && selectedStep ? (
                <aside className="min-w-0 lg:col-span-2 xl:col-span-1">
                  <ConversationPanel
                    view={view}
                    step={selectedStep}
                    busy={busy}
                    standalone
                    onFeedback={handleFeedback}
                    onAccept={(proposal) => void handleAccept(proposal)}
                    onReject={(proposal) => void handleReject(proposal)}
                    onReplay={() => void handleReplay()}
                  />
                </aside>
              ) : null}
            </div>
          </>
        )}
      </div>

      <NewRunDialog
        open={newRunOpen}
        config={config}
        busy={creating}
        onClose={() => setNewRunOpen(false)}
        onCreate={handleCreate}
      />

      <CompareDialog
        comparison={comparison}
        currentView={view}
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        onOpenRun={(id) => void openRun(id)}
      />

      {error ? (
        <div
          className="fixed bottom-4 right-4 z-[80] flex max-w-[min(420px,calc(100vw-2rem))] items-start gap-2 rounded-xl border border-red-200 bg-white px-3 py-2.5 text-[11px] leading-relaxed text-red-800 shadow-float"
          role="alert"
          aria-live="assertive"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="rounded p-0.5 text-red-400 hover:bg-red-50 hover:text-red-700"
            aria-label="Dismiss error"
          >
            <X size={13} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
