export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  const match = id.match(/run_([a-f0-9]+)/i);
  if (match) return `run_${match[1].slice(0, 6)}`;
  return id.length > 18 ? `${id.slice(0, 12)}…` : id;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function statusTone(status: string): {
  label: string;
  className: string;
  dot: string;
} {
  switch (status) {
    case "completed":
      return {
        label: "Completed",
        className: "bg-emerald-50 text-emerald-700 border-emerald-200",
        dot: "bg-emerald-500",
      };
    case "running":
      return {
        label: "Running",
        className: "bg-sky-50 text-sky-700 border-sky-200",
        dot: "bg-sky-500",
      };
    case "paused":
    case "paused_before_step":
    case "paused_before_operation":
      return {
        label: "Paused",
        className: "bg-accent-50 text-accent-700 border-accent-200",
        dot: "bg-accent-500",
      };
    case "inside":
      return {
        label: "Inside",
        className: "bg-accent-50 text-accent-700 border-accent-200",
        dot: "bg-accent-500",
      };
    case "awaiting_approval":
      return {
        label: "Approval",
        className: "bg-amber-50 text-amber-800 border-amber-200",
        dot: "bg-amber-500",
      };
    case "failed":
      return {
        label: "Failed",
        className: "bg-red-50 text-red-700 border-red-200",
        dot: "bg-red-500",
      };
    case "rejected":
      return {
        label: "Rejected",
        className: "bg-slate-50 text-slate-600 border-slate-200",
        dot: "bg-slate-400",
      };
    case "skipped":
      return {
        label: "Skipped",
        className: "bg-slate-50 text-slate-600 border-slate-200",
        dot: "bg-slate-400",
      };
    default:
      return {
        label: "Pending",
        className: "bg-slate-50 text-slate-600 border-slate-200",
        dot: "bg-slate-300",
      };
  }
}

export function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function asString(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return pretty(value);
}
