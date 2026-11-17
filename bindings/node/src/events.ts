import type { BlockCause } from "./protocol.js";
import type { ExecutionOutcome, NodeFailure } from "./execution.js";

export type NodeEventDetail =
  | { readonly state: "started" | "completed" | "skipped" | "cancelled" }
  | { readonly state: "failed"; readonly failure: NodeFailure }
  | { readonly state: "blocked"; readonly cause: BlockCause };

export type NodeExecutionEvent = NodeEventDetail & {
  readonly sequence: number;
  readonly nodeId: bigint;
  readonly nodePath: readonly number[];
  readonly labelPath: readonly string[];
};

/** An observer failed; all already-dispatched work has settled before delivery. */
export class EventDeliveryError extends Error {
  constructor(
    readonly outcome: ExecutionOutcome,
    cause: unknown,
  ) {
    super("execution event delivery failed", { cause });
    this.name = "EventDeliveryError";
  }
}
