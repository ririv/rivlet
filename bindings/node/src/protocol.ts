/** Private versioned-by-package wire shapes; application values never cross them. */
export type Prerequisite =
  | { kind: "required"; node: number }
  | { kind: "static"; node: number; enabled: boolean }
  | { kind: "conditional"; node: number; condition: number };
export type Activation =
  | { kind: "active"; prerequisites: Prerequisite[]; condition: number | null }
  | { kind: "inactive" };
export type DeclaredNode =
  | { kind: "step"; graph: number; activation: Activation }
  | {
      kind: "graph";
      graph: number;
      childGraph: number;
      activation: Activation;
    };
export type DeclaredScope = { kind: "root" } | { kind: "owned"; node: number };
export type PortConnection =
  | { kind: "required"; source: number; target: number }
  | { kind: "conditional"; source: number; target: number; condition: number };
export interface Declaration {
  graphs: DeclaredScope[];
  nodes: DeclaredNode[];
  conditions: number[][];
  ports: PortConnection[];
}
export type ConditionSite =
  | { kind: "node"; condition: number }
  | { kind: "edge"; condition: number; prerequisite: number };
export type FailureCause = { kind: "action" } | { kind: "child"; node: number };
export type BlockCause =
  | { kind: "prerequisite"; node: number }
  | { kind: "parent"; node: number }
  | { kind: "condition"; site: ConditionSite }
  | { kind: "conditionInput"; site: ConditionSite; input: number };
export type CoreEvent =
  | { kind: "cancellationRequested" }
  | { kind: "conditionEvaluated"; node: number; value: boolean }
  | {
      kind: "conditionSkipped" | "graphEntered" | "graphCompleted" | "skipped" | "cancelled";
      node: number;
    }
  | { kind: "failed"; node: number; cause: FailureCause }
  | { kind: "blocked"; node: number; cause: BlockCause };
export interface Batch {
  actions: number[];
  conditions: { node: number; condition: number }[];
  events: CoreEvent[];
  status: "running" | "succeeded" | "failed" | "cancelled";
}
export type Completion = { kind: "succeeded" | "failed" | "cancelled"; node: number };
export type ConditionResolution =
  | { kind: "resolved"; condition: number; value: boolean }
  | { kind: "failed" | "cancelled"; condition: number };
export interface Update {
  completed: Completion[];
  conditions: ConditionResolution[];
  skipped: number[];
}
export type Admission =
  | { kind: "unbounded" }
  | { kind: "limited"; maxima: number[]; memberships: number[][] };
export interface SessionOptions {
  skipped: number[];
  admission: Admission;
}
export type NodeState =
  | "pending"
  | "ready"
  | "running"
  | "active"
  | "succeeded"
  | "skipped"
  | "failed"
  | "blocked"
  | "cancelled";

export interface NativeSession {
  requestCancel(): void;
  start(capacity: number): Batch;
  advance(update: Update, capacity: number): Batch;
  states(): NodeState[];
}
export interface NativeCompilation {
  mappings(): { nodes: number[]; conditions: number[] };
  session(options: SessionOptions): NativeSession;
}
export interface NativeModule {
  packageVersion(): string;
  allocateNodeId(): bigint;
  compileDeclaration(input: Declaration): NativeCompilation;
}
