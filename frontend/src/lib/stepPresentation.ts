/**
 * Presentation-only helpers for turning the runtime's individual tool calls
 * into a small number of readable actions. Execution still happens operation
 * by operation; nothing here changes what the debugger runs.
 */

export interface PlanOperation {
  tool: string;
  args: Record<string, unknown>;
}

export interface ActionGroup<T extends PlanOperation = PlanOperation> {
  key: string;
  operations: T[];
}

const SCHEMA_TOOLS = new Set(["inspect_schema", "inspect_data_dictionary"]);
const PROFILE_TOOLS = new Set(["profile_column", "sample_records"]);

const TOOL_LABELS: Record<string, string> = {
  inspect_schema: "Inspect schema",
  inspect_data_dictionary: "Inspect data dictionary",
  profile_column: "Profile column",
  sample_records: "Sample records",
  filter_rows: "Filter rows",
  join_tables: "Join tables",
  group_and_aggregate: "Group and aggregate",
  compute_metric: "Compute metric",
  compose_report: "Compose report",
  sandbox_inspect: "Inspect sandbox",
  sandbox_delete_rows: "Delete sandbox rows",
  sandbox_customer_preview: "Preview inactive customers",
  sandbox_delete_inactive_customers: "Delete inactive customers",
  sandbox_archive_inactive_customers: "Archive inactive customers",
  sandbox_customer_audit: "Audit customer rows",
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function listArg(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  return value === undefined || value === null ? [] : [value];
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function code(value: unknown): string {
  const valueText = text(value);
  return valueText && valueText !== "—" ? `\`${valueText}\`` : "the target";
}

function listText(items: string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0];
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

function unique(items: string[]): string[] {
  return items.filter((item, index) => item && items.indexOf(item) === index);
}

function stripPeriod(value: string): string {
  return value.replace(/\.+$/, "");
}

function withPeriod(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

export function humanMetricName(value: string): string {
  return value.replace(/[_-]+/g, " ").toLowerCase();
}

function naturalOperator(operator: unknown): string {
  switch (operator) {
    case "eq":
      return "is";
    case "neq":
    case "ne":
      return "is not";
    case "gt":
      return "is greater than";
    case "gte":
      return "is at least";
    case "lt":
      return "is less than";
    case "lte":
      return "is at most";
    case "in":
      return "is one of";
    case "not_in":
      return "is not one of";
    case "contains":
      return "contains";
    case "is_null":
      return "is empty";
    case "not_null":
      return "is present";
    default:
      return text(operator);
  }
}

function naturalWhere(value: unknown): string {
  const conditions = listArg(value)
    .map((item) => {
      const condition = asRecord(item);
      if (!condition.column) return "";
      if (condition.op === "is_null") return `${code(condition.column)} is empty`;
      if (condition.op === "not_null") return `${code(condition.column)} is present`;
      const operator = naturalOperator(condition.op);
      const values = Array.isArray(condition.value)
        ? listText(condition.value.map(code))
        : code(condition.value);
      return `${code(condition.column)} ${operator} ${values}`;
    })
    .filter(Boolean);
  return conditions.join(" and ");
}

function aggregationText(value: unknown): string {
  return listText(
    listArg(value).map((item) => {
      const aggregation = asRecord(item);
      const alias = aggregation.alias ? ` as ${code(aggregation.alias)}` : "";
      return `${text(aggregation.func).toLowerCase()} ${code(aggregation.column)}${alias}`;
    }),
  );
}

function actionKey(tool: string): string {
  if (SCHEMA_TOOLS.has(tool)) return "schema";
  if (PROFILE_TOOLS.has(tool)) return "profile";
  switch (tool) {
    case "filter_rows":
      return "filter";
    case "join_tables":
      return "join";
    case "group_and_aggregate":
      return "group";
    case "compute_metric":
      return "metric";
    case "compose_report":
      return "report";
    case "sandbox_inspect":
      return "sandbox_read";
    case "sandbox_delete_rows":
      return "sandbox_write";
    case "sandbox_customer_preview":
      return "customer_preview";
    case "sandbox_delete_inactive_customers":
      return "customer_delete";
    case "sandbox_archive_inactive_customers":
      return "customer_archive";
    case "sandbox_customer_audit":
      return "customer_audit";
    default:
      return `tool:${tool}`;
  }
}

function schemaLabel(operations: PlanOperation[]): string {
  const tables = unique(operations.map((op) => text(op.args.table)).filter((name) => name !== "—"));
  const hasDictionary = operations.some((op) => op.tool === "inspect_data_dictionary");
  if (tables.length === 0) {
    return hasDictionary
      ? "Read the documented field meanings for the source tables."
      : "Inspect the source schemas.";
  }
  const noun = tables.length === 1 ? "schema" : "schemas";
  const dictionary = hasDictionary ? " and documented field meanings" : "";
  return `Inspect the ${noun}${dictionary} of ${listText(tables.map(code))}.`;
}

function profileLabel(operations: PlanOperation[]): string {
  const profileOperations = operations.filter((op) => op.tool === "profile_column");
  const sampleOperations = operations.filter((op) => op.tool === "sample_records");
  const columns = unique(
    profileOperations.map((op) => {
      const table = text(op.args.table);
      const column = text(op.args.column);
      return table !== "—" && column !== "—" ? `${table}.${column}` : "";
    }),
  );
  const sampleTables = unique(
    sampleOperations.map((op) => text(op.args.table)).filter((name) => name !== "—"),
  );
  const parts: string[] = [];

  if (columns.some((column) => /(^|\.)customer_(unique_)?id$/.test(column))) {
    parts.push("Check the customer identifiers and their uniqueness.");
  } else if (columns.length > 0) {
    parts.push(`Check ${listText(columns.map(code))} for uniqueness and repeated values.`);
  }
  if (sampleTables.length > 0) {
    parts.push(`Read a few sample rows from ${listText(sampleTables.map(code))}.`);
  }
  return parts.join(" ") || "Inspect the source columns.";
}

function filterLabel(operations: PlanOperation[]): string {
  return operations
    .map((op) => {
      const table = code(op.args.table);
      const where = naturalWhere(op.args.where);
      return where ? `Keep ${table} rows where ${where}.` : `Keep all ${table} rows.`;
    })
    .join(" Then ");
}

function joinLabel(operations: PlanOperation[]): string {
  if (operations.length === 0) return "Join the source tables.";
  if (operations.length === 1) {
    const args = operations[0].args;
    const keys = listText(listArg(args.left_on).map(code));
    const output = args.output ? ` into ${code(args.output)}` : "";
    return `Join ${code(args.left)} with ${code(args.right)}${
      keys ? ` on ${keys}` : ""
    }${output}.`;
  }
  const first = operations[0].args;
  const last = operations[operations.length - 1].args;
  const rights = unique(
    operations.map((op) => text(op.args.right)).filter((name) => name !== "—"),
  );
  const output = last.output ? ` into ${code(last.output)}` : "";
  return `Join ${code(first.left)} with ${listText(rights.map(code))}${output}.`;
}

function groupLabel(operations: PlanOperation[]): string {
  return operations
    .map((op) => {
      const args = op.args;
      const datePart = asRecord(args.date_part);
      const keys = [
        ...(datePart.column
          ? [`${text(datePart.unit) || "date"} of ${code(datePart.column)}`]
          : []),
        ...listArg(args.by).map((key) => code(key)),
      ];
      const aggregation = aggregationText(args.aggregations);
      return `Group ${code(args.table)} by ${
        keys.length > 0 ? listText(keys) : "the stated keys"
      }${aggregation ? `; ${aggregation}` : ""}.`;
    })
    .join(" Then ");
}

function metricLabel(operations: PlanOperation[]): string {
  const names = operations.map((op) => humanMetricName(text(op.args.name)));
  if (operations.length === 1) {
    const table = operations[0].args.table;
    return `Calculate the ${names[0]}${table ? ` from ${code(table)}` : ""}.`;
  }
  return `Calculate ${listText(names)}.`;
}

function reportLabel(operations: PlanOperation[]): string {
  const names = unique(
    operations.flatMap((op) =>
      listArg(op.args.metric_names)
        .map((name) => humanMetricName(text(name)))
        .filter(Boolean),
    ),
  );
  if (names.length === 1) return `Generate the final report for the ${names[0]}.`;
  if (names.length > 1) return `Generate the final report for ${listText(names)}.`;
  return "Generate the final report.";
}

function sandboxWriteLabel(operations: PlanOperation[]): string {
  const where = naturalWhere(operations[0]?.args.where);
  return `Delete sandbox orders${where ? ` where ${where}` : ""}; approval required.`;
}

function customerWindow(operations: PlanOperation[]): string {
  const args = operations[0]?.args ?? {};
  const months = typeof args.months === "number" ? args.months : 12;
  const reference =
    args.reference === "dataset_latest" ? "the dataset's latest order" : "the current date";
  return `${months} months before ${reference}`;
}

function rawDescription(key: string, operations: PlanOperation[]): string {
  switch (key) {
    case "schema":
      return schemaLabel(operations);
    case "profile":
      return profileLabel(operations);
    case "filter":
      return filterLabel(operations);
    case "join":
      return joinLabel(operations);
    case "group":
      return groupLabel(operations);
    case "metric":
      return metricLabel(operations);
    case "report":
      return reportLabel(operations);
    case "sandbox_read":
      return "Check the row counts and order statuses in the disposable sandbox.";
    case "sandbox_write":
      return sandboxWriteLabel(operations);
    case "customer_preview":
      return `Preview customer rows inactive for ${customerWindow(operations)}; no rows change.`;
    case "customer_delete":
      return `Delete inactive customers from the disposable sandbox using ${customerWindow(
        operations,
      )}; approval required.`;
    case "customer_archive":
      return `Mark inactive customers as archived in the disposable sandbox using ${customerWindow(
        operations,
      )}; no rows are removed.`;
    case "customer_audit":
      return "Verify sandbox customer counts, archived rows, and the source customer table; no rows change.";
    case "mixed": {
      const subgroups: ActionGroup[] = [];
      for (const op of operations) {
        const keyForOperation = actionKey(op.tool);
        const previous = subgroups[subgroups.length - 1];
        if (previous && previous.key === keyForOperation) {
          previous.operations.push(op);
        } else {
          subgroups.push({ key: keyForOperation, operations: [op] });
        }
      }
      return subgroups
        .map((group) => stripPeriod(rawDescription(group.key, group.operations)))
        .join(" Then ");
    }
    default: {
      const label =
        TOOL_LABELS[operations[0]?.tool] ??
        text(operations[0]?.tool).replace(/_/g, " ");
      return `${label || "Run the step operations"}.`;
    }
  }
}

/** One to three concise action groups; technical tool calls stay underneath. */
export function groupPlanOperations<T extends PlanOperation>(operations: T[]): ActionGroup<T>[] {
  const groups: ActionGroup<T>[] = [];
  for (const operation of operations) {
    const key = actionKey(operation.tool);
    const previous = groups[groups.length - 1];
    if (previous && previous.key === key) {
      previous.operations.push(operation);
    } else {
      groups.push({ key, operations: [operation] });
    }
  }

  while (groups.length > 3) {
    let mergeAt = 0;
    let smallest = Number.POSITIVE_INFINITY;
    for (let index = 0; index < groups.length - 1; index += 1) {
      const size = groups[index].operations.length + groups[index + 1].operations.length;
      if (size < smallest) {
        smallest = size;
        mergeAt = index;
      }
    }
    const merged: ActionGroup<T> = {
      key: "mixed",
      operations: [...groups[mergeAt].operations, ...groups[mergeAt + 1].operations],
    };
    groups.splice(mergeAt, 2, merged);
  }
  return groups;
}

export function describeAction(action: ActionGroup): string {
  return withPeriod(rawDescription(action.key, action.operations));
}

export function planOperationsFrom(value: unknown): PlanOperation[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      const operation = asRecord(item);
      const tool = text(operation.tool);
      if (!tool || tool === "—") return null;
      return { tool, args: asRecord(operation.args) } satisfies PlanOperation;
    })
    .filter((operation): operation is PlanOperation => operation !== null);
}
