export type RunStatus =
  | "paused_before_step"
  | "paused_before_operation"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface Fact {
  label: string;
  value: string;
}

export interface EvidenceItem {
  id: string;
  operation_id: string;
  kind: string;
  status: "OBSERVED" | "PREVIEW" | "PROPOSED";
  label: string;
  summary: string;
  facts: Fact[];
  details: Record<string, unknown>;
  source_artifact_ids: string[];
  created_at: string;
}

export interface Operation {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  status: string;
  summary: string;
  requires_approval: boolean;
  input_artifact_ids: string[];
  output_artifact_ids: string[];
  error: string | null;
  evidence: EvidenceItem[];
}

export interface DependencyNode {
  label: string;
  step_id: string | null;
  status: "PLANNED" | "OBSERVED";
}

export interface SemanticBinding {
  concept: string;
  source_table: string;
  column: string;
  status: string;
  note: string;
  evidence_refs: string[];
}

export interface MetricDefinition {
  name: string;
  definition: string;
  unit: string;
  status: string;
}

export interface TaskSpec {
  id: string;
  run_id: string;
  version: number;
  objective: string;
  population: string;
  time_scope: string;
  metric_definitions: MetricDefinition[];
  semantic_bindings: SemanticBinding[];
  assumptions: string[];
  created_at: string;
}

export interface MetricValue {
  name: string;
  value: number | null;
  formatted: string;
  numerator: number | null;
  denominator: number | null;
  unit: string;
  definition: string;
  source_step_id: string | null;
  source_artifact_id: string | null;
  extras: Record<string, unknown>;
}

export interface Report {
  title: string;
  summary: string;
  metrics: MetricValue[];
  table_name?: string | null;
  table_rows?: number | null;
  notes: string[];
  step_links: { step_id: string; title: string }[];
  created_at: string;
}

export interface Message {
  id: string;
  step_id: string;
  role: "user" | "agent" | "system";
  text: string;
  created_at: string;
  proposal_id: string | null;
}

export interface RevisionChange {
  kind: "binding" | "metric" | "plan" | "assumption";
  description: string;
  before: string;
  after: string;
}

export interface StepUpdate {
  step_id: string;
  title?: string | null;
  goal?: string | null;
  understanding?: string | null;
  target_data?: string[] | null;
  rationale?: string | null;
  operations?: Record<string, unknown>[] | null;
}

export interface Investigation {
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  facts: Fact[];
}

export interface Proposal {
  id: string;
  run_id: string;
  step_id: string;
  user_text: string;
  explanation: string;
  changes: RevisionChange[];
  proposed_bindings: SemanticBinding[] | null;
  proposed_metrics: MetricDefinition[] | null;
  proposed_assumptions: string[] | null;
  step_updates: StepUpdate[];
  investigations: Investigation[];
  earliest_affected_step: number;
  execution_changed: boolean;
  status: "pending" | "accepted" | "rejected" | "no_change";
  created_at: string;
}

export interface StepView {
  id: string;
  index: number;
  number: number;
  title: string;
  goal: string;
  status: string;
  base_status: string;
  inherited: boolean;
  is_current: boolean;
  is_inside: boolean;
  is_read_only: boolean;
  is_skipped: boolean;
  understanding: string;
  target_data: string[];
  rationale: string;
  operations: Operation[];
  evidence: EvidenceItem[];
  preview_evidence: EvidenceItem[];
  dependency_chain: DependencyNode[];
  messages: Message[];
  proposals: Proposal[];
  active_proposal: Proposal | null;
  can_feedback: boolean;
}

export type BreakpointOperation = "delete" | "drop";

export type BreakpointKind = "operation" | "filter_excludes" | "data_effect";

export interface BreakpointCondition {
  column: "order_status";
  value: "canceled";
  metric?: "max_copies_per_payment_record" | null;
  threshold?: number | null;
}

export interface BreakpointRule {
  id: string;
  natural_language: string;
  label: string;
  operation_types: BreakpointOperation[];
  kind: BreakpointKind;
  condition: BreakpointCondition | null;
  phase: "before";
  active: boolean;
  created_at: string;
}

export interface BreakpointDraft {
  natural_language: string;
  label: string;
  operation_types: BreakpointOperation[];
  kind: BreakpointKind;
  condition: BreakpointCondition | null;
  phase: "before";
}

export interface BreakpointInterpretation {
  supported: boolean;
  message: string;
  rule: BreakpointDraft | null;
}

export interface Approval {
  id: string;
  operation_id: string;
  step_id: string;
  tool: string;
  args: Record<string, unknown>;
  label: string;
  facts: Fact[];
  status: "pending" | "approved" | "rejected";
  reason: string | null;
  matched_breakpoint: BreakpointRule | null;
  mandatory: boolean;
  created_at: string;
}

export interface Capabilities {
  step_into: boolean;
  step_over: boolean;
  step_out: boolean;
  continue: boolean;
  pause: boolean;
  replay: boolean;
  compare: boolean;
  feedback: boolean;
  approve: boolean;
  reject: boolean;
}

export interface RunView {
  id: string;
  parent_run_id: string | null;
  replay_of_revision_id: string | null;
  task: string;
  mode: "scripted" | "live";
  dataset: string;
  scenario_id: string;
  reference_kind: string | null;
  planner_label: string;
  status: RunStatus;
  status_label: string;
  cursor: { step_index: number; op_index: number; inside: boolean };
  steps: StepView[];
  task_spec: TaskSpec;
  task_spec_history: TaskSpec[];
  metrics: Record<string, MetricValue>;
  report: Report | null;
  reference_check: ReferenceCheck | null;
  revisions: Proposal[];
  messages: Message[];
  accepted_revision_id: string | null;
  child_run_ids: string[];
  approval: Approval | null;
  approval_history: Approval[];
  breakpoints: BreakpointRule[];
  capabilities: Capabilities;
  error: string | null;
  event_count: number;
  events_tail: { ts: string; type: string; message: string }[];
  idempotent_replay: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReferenceCheck {
  kind?: string;
  definition?: string;
  workflow_value?: number | null;
  workflow_formatted?: string;
  reference_value?: number | null;
  reference_formatted?: string;
  difference?: number | null;
  agreement?: boolean;
  numerator?: number;
  denominator?: number;
  grouped_agreement?: boolean | null;
  max_group_difference?: number | null;
  mismatched_groups?: number | null;
  comparison_error?: string | null;
  num_groups?: number;
  diagnostics?: Record<string, unknown>;
  error?: string;
  [key: string]: unknown;
}

export interface RunSummary {
  id: string;
  parent_run_id: string | null;
  task: string;
  mode: string;
  scenario_id: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  metric_summary: string;
}

export interface LiveStatus {
  available: boolean;
  provider: string | null;
  model: string | null;
  reason: string | null;
}

export interface AppConfig {
  live: LiveStatus;
  scripted: { available: boolean };
  datasets: { id: string; label: string }[];
  scenarios: { id: string; label: string }[];
  default_task: string;
  version: string;
}

export interface Comparison {
  parent_run_id: string;
  child_run_id: string;
  revision_id: string | null;
  affected_from_step: number;
  parent_status: RunStatus;
  child_status: RunStatus;
  binding_diff: { concept: string; old: string; new: string }[];
  metric_definitions_diff: {
    name: string;
    old_value: number | null;
    old_formatted: string;
    new_value: number | null;
    new_formatted: string;
    old_definition: string | null;
    new_definition: string | null;
  }[];
  plan_diff: {
    step_index: number;
    step_number: number;
    title: string;
    old_operations: string[];
    new_operations: string[];
    changed_operations: boolean;
    old_understanding: string;
    new_understanding: string;
  }[];
  invalidated: { step_number: number; title: string; operations: string[] }[];
  parent_metrics: Record<string, MetricValue>;
  child_metrics: Record<string, MetricValue>;
  parent_reference_check: ReferenceCheck | null;
  child_reference_check: ReferenceCheck | null;
  child_report: Report | null;
  revision_explanation: string | null;
  revision_changes: RevisionChange[];
}

export interface ReplayResponse {
  run: RunView;
  comparison: Comparison;
}
