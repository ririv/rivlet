import { Compilation, createSession, type MountedNode } from "./compilation.js";
import {
  Graph,
  invokeAction,
  type Node,
  type ResultSource,
  type NodeRunContext,
} from "./model.js";
import { Some, nothing, type Option } from "./option.js";
import {
  EventDeliveryError,
  type NodeEventDetail,
  type NodeExecutionEvent,
} from "./events.js";
import { ActionBudget, type Budget } from "./budget.js";
import { sharedAccess, type SharedAccess } from "./shared.js";
import type {
  Admission,
  Batch,
  BlockCause,
  Completion,
  ConditionResolution,
  CoreEvent,
  NodeState,
} from "./protocol.js";

export type NodeOutcome<T = unknown> =
  | { readonly state: "succeeded"; readonly value: T }
  | { readonly state: "skipped" }
  | { readonly state: "cancelled" }
  | { readonly state: "failed"; readonly failure: NodeFailure }
  | { readonly state: "blocked"; readonly cause: BlockCause };
export type NodeFailure =
  | { readonly kind: "action"; readonly error: unknown }
  | { readonly kind: "expansion"; readonly error: unknown }
  | { readonly kind: "subgraph" }
  | { readonly kind: "child"; readonly node: number };
export interface RunOptions {
  readonly shared?: object;
  readonly events?: (event: NodeExecutionEvent) => void;
  readonly signal?: AbortSignal;
  readonly maxConcurrency?: ReadonlyMap<Node, number>;
  readonly skip?: readonly Node[];
}

export class ExecutionOutcome {
  readonly #results: ReadonlyMap<number, Option<unknown>>;
  readonly #outcomes: readonly NodeOutcome[];
  readonly #conditionErrors: ReadonlyMap<number, unknown>;
  readonly #subgraphs: ReadonlyMap<number, ExecutionOutcome>;
  constructor(
    readonly compilation: Compilation,
    readonly status: "succeeded" | "failed" | "cancelled",
    outcomes: readonly NodeOutcome[],
    values: ReadonlyMap<number, Option<unknown>>,
    conditionErrors: ReadonlyMap<number, unknown>,
    subgraphs: ReadonlyMap<number, ExecutionOutcome> = new Map(),
  ) {
    this.#outcomes = Object.freeze([...outcomes]);
    this.#results = new Map(values);
    this.#conditionErrors = new Map(conditionErrors);
    this.#subgraphs = new Map(subgraphs);
    Object.freeze(this);
  }
  subgraph(generator: Node): ExecutionOutcome | undefined {
    return this.#subgraphs.get(this.compilation.uniqueIndex(generator));
  }
  conditionError(index: number): Option<unknown> {
    if (
      !Number.isSafeInteger(index) ||
      index < 0 ||
      index >= this.compilation.conditions.length
    )
      throw new Error("condition index is outside this compilation");
    return this.#conditionErrors.has(index)
      ? new Some(this.#conditionErrors.get(index))
      : nothing;
  }
  result<T>(source: ResultSource<T>): Option<T> {
    const index = this.compilation.resultIndex(source);
    const outcome = this.node(index);
    if (
      outcome.state === "failed" ||
      outcome.state === "blocked" ||
      outcome.state === "cancelled"
    )
      throw new Error("failed/blocked nodes have no successful result");
    const value = this.#results.get(index);
    if (value === undefined) throw new Error("terminal result is missing");
    // 类型参数来自同一 Step/OutputPort；不对 JS 任意对象进行序列化或猜测转换。
    return value as Option<T>;
  }
  node(index: number): NodeOutcome {
    const value = this.#outcomes[index];
    if (value === undefined)
      throw new Error("node index is outside this compilation");
    return value;
  }
  nodes(): readonly {
    readonly mount: MountedNode;
    readonly outcome: NodeOutcome;
  }[] {
    return Object.freeze(
      this.compilation.nodes.map((mount) =>
        Object.freeze({ mount, outcome: this.node(mount.index) }),
      ),
    );
  }
}

export class ExecutionError extends Error {
  constructor(readonly outcome: ExecutionOutcome) {
    super("graph execution failed");
    this.name = "ExecutionError";
  }
}

export class ExecutionCancelled extends Error {
  constructor(readonly outcome: ExecutionOutcome) {
    super("graph execution cancelled");
    this.name = "ExecutionCancelled";
  }
}

function admission(
  compilation: Compilation,
  limits: ReadonlyMap<Node, number> | undefined,
): Admission {
  if (limits === undefined || limits.size === 0) return { kind: "unbounded" };
  const scopes = new Map<number, number>();
  const maxima: number[] = [];
  for (const [node, maximum] of limits) {
    if (!Number.isSafeInteger(maximum) || maximum <= 0)
      throw new Error("maxConcurrency values must be positive safe integers");
    scopes.set(compilation.uniqueIndex(node), maxima.length);
    maxima.push(maximum);
  }
  const memberships = compilation.nodes.map((record) => {
    const path: number[] = [];
    let index: number | null = record.index;
    while (index !== null) {
      const scope = scopes.get(index);
      if (scope !== undefined) path.push(scope);
      const owner: MountedNode | undefined = compilation.nodes[index];
      if (owner === undefined) throw new Error("invalid compiled owner");
      index = owner.owner;
    }
    return path;
  });
  return { kind: "limited", maxima, memberships };
}

type Settled =
  | { readonly kind: "action"; readonly value: Completion }
  | { readonly kind: "condition"; readonly value: ConditionResolution };

interface Environment {
  readonly shared: SharedAccess;
  readonly internal: AbortController;
  readonly signal: AbortSignal;
  readonly budget: ActionBudget;
  sequence: number;
  delivery: { kind: "active" } | { kind: "failed"; error: unknown };
  readonly events: ((event: NodeExecutionEvent) => void) | undefined;
}
interface Scope {
  readonly environment: Environment;
  readonly path: readonly number[];
  readonly labels: readonly string[];
  readonly inherited: readonly Budget[];
}

function acknowledgedCancellation(
  signal: AbortSignal,
  error: unknown,
): boolean {
  // Node 的定时器会用 AbortError.cause 包装 signal.reason，不能只比较外层对象。
  return (
    signal.aborted &&
    (Object.is(error, signal.reason) ||
      (error instanceof Error &&
        error.name === "AbortError" &&
        "cause" in error &&
        Object.is(error.cause, signal.reason)))
  );
}

/** Single-use Promise driver; readiness, admission and failure propagation stay in Rust. */
export class Execution {
  #compilation: Compilation | undefined;
  #started = false;
  constructor(readonly root: Node) {}
  compile(): Compilation {
    return (this.#compilation ??= new Compilation(this.root));
  }
  async run(options: RunOptions = {}): Promise<ExecutionOutcome> {
    return this.#run(options);
  }
  async #run(options: RunOptions, parent?: Scope): Promise<ExecutionOutcome> {
    if (this.#started) throw new Error("an Execution can run only once");
    const compilation = this.compile();
    this.#started = true;
    const session = compilation[createSession]({
      skipped: (options.skip ?? []).map((node) =>
        compilation.uniqueIndex(node),
      ),
      admission: admission(compilation, options.maxConcurrency),
    });
    const capacity = Math.max(compilation.nodes.length, 1);
    const results = new Map<number, Option<unknown>>();
    const failures = new Map<number, NodeFailure>();
    const subgraphs = new Map<number, ExecutionOutcome>();
    const conditionErrors = new Map<number, unknown>();
    const events = new Map<number, CoreEvent>();
    const pending = new Set<`action:${number}` | `condition:${number}`>();
    const internal = parent?.environment.internal ?? new AbortController();
    const signal =
      parent?.environment.signal ??
      (options.signal === undefined
        ? internal.signal
        : AbortSignal.any([options.signal, internal.signal]));
    const environment: Environment = parent?.environment ?? {
      shared: sharedAccess(options.shared),
      internal,
      signal,
      sequence: 0,
      delivery: { kind: "active" },
      events: options.events,
      budget: new ActionBudget(),
    };
    const groups = new Map<number, Budget>();
    for (const [node, maximum] of options.maxConcurrency ?? [])
      groups.set(compilation.uniqueIndex(node), { maximum, running: 0 });
    const budgets = (index: number): readonly Budget[] => {
      const values = [...(parent?.inherited ?? [])];
      let current: number | null = index;
      while (current !== null) {
        const group = groups.get(current);
        if (group !== undefined) values.push(group);
        const mount: MountedNode | undefined = compilation.nodes[current];
        if (mount === undefined)
          throw new Error("budget owner is outside compilation");
        current = mount.owner;
      }
      return values;
    };
    const path = (record: MountedNode): readonly number[] =>
      Object.freeze([...(parent?.path ?? []), ...record.nodePath]);
    const labels = (record: MountedNode): readonly string[] =>
      Object.freeze([...(parent?.labels ?? []), ...record.labelPath]);
    const emit = (index: number, detail: NodeEventDetail): void => {
      if (
        environment.events === undefined ||
        environment.delivery.kind === "failed"
      )
        return;
      const record = compilation.nodes[index];
      if (record === undefined)
        throw new Error("event node is outside compilation");
      if (detail.state === "failed")
        detail = { ...detail, failure: Object.freeze({ ...detail.failure }) };
      if (detail.state === "blocked") {
        const cause =
          "site" in detail.cause
            ? { ...detail.cause, site: Object.freeze({ ...detail.cause.site }) }
            : { ...detail.cause };
        detail = { ...detail, cause: Object.freeze(cause) };
      }
      const event = Object.freeze({
        ...detail,
        sequence: environment.sequence++,
        nodeId: record.node.id,
        nodePath: path(record),
        labelPath: labels(record),
      });
      try {
        environment.events(event);
      } catch (error: unknown) {
        environment.delivery = { kind: "failed", error };
        internal.abort(error);
      }
    };
    const inbox: Settled[] = [];
    let cursor = 0;
    let wake: (() => void) | undefined;
    const abort = (): void => {
      if (!signal.aborted) return;
      session.requestCancel();
      const notify = wake;
      wake = undefined;
      notify?.();
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    const complete = (settled: Settled): void => {
      inbox.push(settled);
      const notify = wake;
      wake = undefined;
      notify?.();
    };
    const result = (index: number): Option<unknown> => {
      const value = results.get(index);
      if (value === undefined)
        throw new Error("scheduled input producer has no result");
      return value;
    };
    let batch: Batch;
    try {
      batch = session.start(capacity);
      for (;;) {
        for (const event of batch.events) {
          switch (event.kind) {
            case "graphEntered":
              emit(event.node, { state: "started" });
              break;
            case "graphCompleted":
              emit(event.node, { state: "completed" });
              break;
            case "skipped":
              emit(event.node, { state: "skipped" });
              break;
            case "cancelled":
              emit(event.node, { state: "cancelled" });
              break;
            case "blocked":
              emit(event.node, { state: "blocked", cause: event.cause });
              break;
            case "failed": {
              const failure =
                event.cause.kind === "action"
                  ? failures.get(event.node)
                  : event.cause;
              if (failure === undefined)
                throw new Error("failure witness is missing");
              emit(event.node, { state: "failed", failure });
              break;
            }
          }
          if (event.kind === "graphCompleted")
            results.set(event.node, new Some(undefined));
          if (event.kind === "skipped" || event.kind === "conditionSkipped")
            results.set(event.node, nothing);
          if (event.kind === "failed" || event.kind === "blocked")
            events.set(event.node, event);
        }
        for (const index of batch.actions) {
          const record = compilation.nodes[index];
          if (record === undefined || record.node.kind !== "step")
            throw new Error("core scheduled a non-action node");
          const node = record.node;
          const context: NodeRunContext = Object.freeze({
            shared: environment.shared,
            signal,
            nodeId: node.id,
            nodePath: path(record),
            labelPath: labels(record),
          });
          pending.add(`action:${index}`);
          void Promise.resolve()
            .then(async (): Promise<Settled> => {
              try {
                const limits = budgets(index);
                const release =
                  limits.length === 0
                    ? undefined
                    : await environment.budget.acquire(limits, signal);
                let value: unknown;
                try {
                  emit(index, { state: "started" });
                  signal.throwIfAborted();
                  value = await node[invokeAction](
                    context,
                    record.inputs.map(result),
                  );
                } finally {
                  release?.();
                }
                if (value instanceof Graph) {
                  const execution = new Execution(value);
                  try {
                    execution.compile();
                  } catch (error: unknown) {
                    failures.set(index, { kind: "expansion", error });
                    return {
                      kind: "action",
                      value: { kind: "failed", node: index },
                    };
                  }
                  try {
                    subgraphs.set(
                      index,
                      await execution.#run(
                        {},
                        {
                          environment,
                          path: [...context.nodePath, 0],
                          labels: context.labelPath,
                          inherited: limits,
                        },
                      ),
                    );
                  } catch (error: unknown) {
                    if (error instanceof ExecutionError) {
                      subgraphs.set(index, error.outcome);
                      failures.set(index, { kind: "subgraph" });
                      return {
                        kind: "action",
                        value: { kind: "failed", node: index },
                      };
                    }
                    if (error instanceof ExecutionCancelled) {
                      subgraphs.set(index, error.outcome);
                      throw signal.reason;
                    }
                    throw error;
                  }
                }
                results.set(index, new Some(value));
                emit(index, { state: "completed" });
                return {
                  kind: "action",
                  value: { kind: "succeeded", node: index },
                };
              } catch (error: unknown) {
                if (acknowledgedCancellation(signal, error)) {
                  return {
                    kind: "action",
                    value: { kind: "cancelled", node: index },
                  };
                }
                failures.set(index, { kind: "action", error });
                return {
                  kind: "action",
                  value: { kind: "failed", node: index },
                };
              }
            })
            .then(complete);
        }
        for (const request of batch.conditions) {
          const bound = compilation.conditions[request.condition];
          if (bound === undefined)
            throw new Error("unknown requested predicate");
          pending.add(`condition:${request.condition}`);
          void Promise.resolve()
            .then(async (): Promise<Settled> => {
              try {
                signal.throwIfAborted();
                const inputs = bound.inputs.map(result);
                const value = await bound.predicate(...(inputs as never[]));
                if (typeof value !== "boolean")
                  throw new Error("a condition must return a boolean");
                return {
                  kind: "condition",
                  value: {
                    kind: "resolved",
                    condition: request.condition,
                    value,
                  },
                };
              } catch (error: unknown) {
                if (acknowledgedCancellation(signal, error)) {
                  return {
                    kind: "condition",
                    value: { kind: "cancelled", condition: request.condition },
                  };
                }
                conditionErrors.set(request.condition, error);
                return {
                  kind: "condition",
                  value: { kind: "failed", condition: request.condition },
                };
              }
            })
            .then(complete);
        }
        if (batch.status !== "running" && pending.size === 0) break;
        if (pending.size === 0 && signal.aborted) {
          batch = session.advance(
            { completed: [], conditions: [], skipped: [] },
            capacity,
          );
          continue;
        }
        if (pending.size === 0)
          throw new Error(
            "running graph has no outstanding action or condition",
          );
        // 每项任务只注册一次完成回调，不在每个完成事件上重建 Promise.race。
        if (cursor === inbox.length)
          await new Promise<void>((resolve) => {
            wake = resolve;
          });
        const completed: Completion[] = [];
        const conditions: ConditionResolution[] = [];
        while (cursor < inbox.length) {
          const settled = inbox[cursor++];
          if (settled === undefined)
            throw new Error("completion queue is inconsistent");
          if (settled.kind === "action") {
            pending.delete(`action:${settled.value.node}`);
            completed.push(settled.value);
          } else {
            pending.delete(`condition:${settled.value.condition}`);
            conditions.push(settled.value);
          }
        }
        inbox.length = 0;
        cursor = 0;
        batch = session.advance(
          { completed, conditions, skipped: [] },
          capacity,
        );
      }
    } finally {
      signal.removeEventListener("abort", abort);
    }
    const outcomes = session
      .states()
      .map((state: NodeState, index): NodeOutcome => {
        const event = events.get(index);
        switch (state) {
          case "succeeded": {
            const value = result(index);
            if (value.kind !== "some")
              throw new Error("successful node has no value");
            return Object.freeze({ state, value: value.value });
          }
          case "skipped":
          case "cancelled":
            return Object.freeze({ state });
          case "failed":
            if (event?.kind !== "failed")
              throw new Error("failure witness is missing");
            if (event.cause.kind === "child")
              return Object.freeze({
                state,
                failure: Object.freeze(event.cause),
              });
            if (!failures.has(index))
              throw new Error("original action error is missing");
            return Object.freeze({
              state,
              failure: Object.freeze(failures.get(index) as NodeFailure),
            });
          case "blocked":
            if (event?.kind !== "blocked")
              throw new Error("blockage witness is missing");
            if ("site" in event.cause) Object.freeze(event.cause.site);
            return Object.freeze({ state, cause: Object.freeze(event.cause) });
          default:
            throw new Error("execution ended with a non-terminal node");
        }
      });
    const outcome = new ExecutionOutcome(
      compilation,
      batch.status,
      outcomes,
      results,
      conditionErrors,
      subgraphs,
    );
    if (parent === undefined && environment.delivery.kind === "failed") {
      throw new EventDeliveryError(outcome, environment.delivery.error);
    }
    if (outcome.status === "failed") throw new ExecutionError(outcome);
    if (outcome.status === "cancelled") throw new ExecutionCancelled(outcome);
    return outcome;
  }
}

export function run(
  root: Node,
  options: RunOptions = {},
): Promise<ExecutionOutcome> {
  return new Execution(root).run(options);
}
