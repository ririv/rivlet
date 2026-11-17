import { Compilation, createSession, type MountedNode } from "./compilation.js";
import {
  Graph,
  invokeAction,
  type Node,
  type ResultSource,
  type NodeRunContext,
} from "./model.js";
import { Some, nothing, type Option } from "./option.js";
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
  | { readonly kind: "child"; readonly node: number };
export interface RunOptions {
  readonly signal?: AbortSignal;
  readonly maxConcurrency?: ReadonlyMap<Node, number>;
  readonly skip?: readonly Node[];
}

export class ExecutionOutcome {
  readonly #results: ReadonlyMap<number, Option<unknown>>;
  readonly #outcomes: readonly NodeOutcome[];
  readonly #conditionErrors: ReadonlyMap<number, unknown>;
  constructor(
    readonly compilation: Compilation,
    readonly status: "succeeded" | "failed" | "cancelled",
    outcomes: readonly NodeOutcome[],
    values: ReadonlyMap<number, Option<unknown>>,
    conditionErrors: ReadonlyMap<number, unknown>,
  ) {
    this.#outcomes = Object.freeze([...outcomes]);
    this.#results = new Map(values);
    this.#conditionErrors = new Map(conditionErrors);
    Object.freeze(this);
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
    if (outcome.state === "failed" || outcome.state === "blocked" || outcome.state === "cancelled")
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

function acknowledgedCancellation(signal: AbortSignal, error: unknown): boolean {
  // Node 的定时器会用 AbortError.cause 包装 signal.reason，不能只比较外层对象。
  return signal.aborted && (
    Object.is(error, signal.reason) ||
    (error instanceof Error && error.name === "AbortError" && "cause" in error && Object.is(error.cause, signal.reason))
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
    const failures = new Map<number, unknown>();
    const conditionErrors = new Map<number, unknown>();
    const events = new Map<number, CoreEvent>();
    const pending = new Set<`action:${number}` | `condition:${number}`>();
    const signal = options.signal ?? new AbortController().signal;
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
          signal,
          nodeId: node.id,
          nodePath: record.nodePath,
          labelPath: record.labelPath,
        });
        pending.add(`action:${index}`);
        void Promise.resolve()
          .then(async (): Promise<Settled> => {
            try {
              signal.throwIfAborted();
              const value = await node[invokeAction](
                context,
                record.inputs.map(result),
              );
              if (value instanceof Graph)
                throw new Error(
                  "runtime-created Graphs are not supported by the Node adapter yet",
                );
              results.set(index, new Some(value));
              return {
                kind: "action",
                value: { kind: "succeeded", node: index },
              };
            } catch (error: unknown) {
              if (acknowledgedCancellation(signal, error)) {
                return { kind: "action", value: { kind: "cancelled", node: index } };
              }
              failures.set(index, error);
              return { kind: "action", value: { kind: "failed", node: index } };
            }
          })
          .then(complete);
      }
      for (const request of batch.conditions) {
        const bound = compilation.conditions[request.condition];
        if (bound === undefined) throw new Error("unknown requested predicate");
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
                return { kind: "condition", value: { kind: "cancelled", condition: request.condition } };
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
        batch = session.advance({ completed: [], conditions: [], skipped: [] }, capacity);
        continue;
      }
      if (pending.size === 0)
        throw new Error("running graph has no outstanding action or condition");
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
      batch = session.advance({ completed, conditions, skipped: [] }, capacity);
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
              failure: Object.freeze({
                kind: "action" as const,
                error: failures.get(index),
              }),
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
    );
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
