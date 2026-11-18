import type { ReactNode } from "react";
import { cx } from "../lib/format";

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "observed" | "preview" | "danger" | "warning";
  className?: string;
}) {
  const tones = {
    neutral: "border-line bg-[#F8F7FC] text-muted",
    accent: "border-accent-200 bg-accent-50 text-accent-700",
    observed: "border-emerald-200 bg-emerald-50 text-emerald-700",
    preview: "border-amber-200 bg-amber-50 text-amber-800",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-red-200 bg-red-50 text-red-700",
  };
  return <span className={cx("chip", tones[tone], className)}>{children}</span>;
}

export function RichText({ text }: { text: string }) {
  const pieces = (text || "").split(/(`[^`]+`)/g);
  return (
    <span className="whitespace-pre-wrap break-words">
      {pieces.map((piece, index) => {
        if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 1) {
          return (
            <code className="code" key={index}>
              {piece.slice(1, -1)}
            </code>
          );
        }
        return <span key={index}>{piece}</span>;
      })}
    </span>
  );
}

export function SectionHeading({
  icon,
  title,
  hint,
  right,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="flex items-center gap-2">
        {icon ? <span className="text-accent-600">{icon}</span> : null}
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
        {hint ? <span className="text-xs text-faint">{hint}</span> : null}
      </div>
      {right}
    </div>
  );
}
