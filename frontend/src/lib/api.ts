import type {
  AppConfig,
  BreakpointInterpretation,
  Comparison,
  ReplayResponse,
  RunSummary,
  RunView,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code = "request_failed", status = 0) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      "Cannot reach the debugger API. Is the FastAPI server running on port 8000?",
      "network_error",
    );
  }
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const body = payload as
      | { error?: { message?: string; code?: string }; detail?: unknown }
      | null;
    let message = body?.error?.message;
    if (!message && typeof body?.detail === "string") message = body.detail;
    if (!message && Array.isArray(body?.detail)) {
      message = body.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: unknown }).msg);
          }
          return String(item);
        })
        .join("; ");
    }
    throw new ApiError(
      message || `Request failed (${response.status}).`,
      body?.error?.code || "request_failed",
      response.status,
    );
  }
  return payload as T;
}

export function commandId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `cmd_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

export const api = {
  config: () => request<AppConfig>("/config"),
  runs: () => request<{ runs: RunSummary[] }>("/runs"),
  run: (id: string) => request<RunView>(`/runs/${id}`),
  createRun: (body: { task: string; mode: "scripted" | "live"; dataset: string }) =>
    request<RunView>("/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  command: (id: string, action: string) =>
    request<RunView>(`/runs/${id}/${action}`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId() }),
    }),
  feedback: (id: string, stepId: string, text: string) =>
    request<RunView>(`/runs/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ step_id: stepId, text, command_id: commandId() }),
    }),
  accept: (id: string, revisionId: string) =>
    request<RunView>(`/runs/${id}/revisions/${revisionId}/accept`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId() }),
    }),
  reject: (id: string, revisionId: string) =>
    request<RunView>(`/runs/${id}/revisions/${revisionId}/reject`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId() }),
    }),
  replay: (id: string, revisionId?: string) =>
    request<ReplayResponse>(`/runs/${id}/replay`, {
      method: "POST",
      body: JSON.stringify({ revision_id: revisionId ?? null, command_id: commandId() }),
    }),
  approve: (id: string) =>
    request<RunView>(`/runs/${id}/approval/approve`, {
      method: "POST",
      body: JSON.stringify({ command_id: commandId() }),
    }),
  rejectApproval: (id: string, reason: string) =>
    request<RunView>(`/runs/${id}/approval/reject`, {
      method: "POST",
      body: JSON.stringify({ reason, command_id: commandId() }),
    }),
  interpretBreakpoint: (id: string, text: string) =>
    request<BreakpointInterpretation>(`/runs/${id}/breakpoints/interpret`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  addBreakpoint: (id: string, text: string) =>
    request<RunView>(`/runs/${id}/breakpoints`, {
      method: "POST",
      body: JSON.stringify({ text, command_id: commandId() }),
    }),
  removeBreakpoint: (id: string, breakpointId: string) =>
    request<RunView>(`/runs/${id}/breakpoints/${breakpointId}`, {
      method: "DELETE",
      body: JSON.stringify({ command_id: commandId() }),
    }),
  setBreakpointActive: (id: string, breakpointId: string, active: boolean) =>
    request<RunView>(`/runs/${id}/breakpoints/${breakpointId}`, {
      method: "PATCH",
      body: JSON.stringify({ active, command_id: commandId() }),
    }),
  compare: (parentId: string, childId?: string) =>
    request<Comparison>(
      `/runs/${parentId}/compare${childId ? `?child_run_id=${encodeURIComponent(childId)}` : ""}`,
    ),
};
