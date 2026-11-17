import { native } from "./backend.js";
import {
  GraphInterface,
  GraphInterfaceView,
  InputPort,
  OutputPort,
} from "./interface.js";
import type { Option } from "./option.js";

export const invokeAction = Symbol("invokeAction");
export const edgeDeclaration = Symbol("edgeDeclaration");
export const resultValueType = Symbol("resultValueType");
export type Awaitable<T> = T | PromiseLike<T>;
export interface NodeRunContext {
  readonly nodeId: bigint;
  readonly nodePath: readonly number[];
  readonly labelPath: readonly string[];
}
type OptionInputs<T extends readonly unknown[]> = {
  [K in keyof T]: Option<T[K]>;
};
export interface StepNode<T = unknown> {
  readonly kind: "step";
  readonly id: bigint;
  readonly label: string;
  readonly condition: Condition | undefined;
  readonly [resultValueType]: T;
  [invokeAction](
    context: NodeRunContext,
    inputs: readonly Option<unknown>[],
  ): Promise<unknown>;
}
export type Node = StepNode | Graph;
export type ResultSource<T = unknown> = StepNode<T> | OutputPort<T>;
export type ResultTarget = StepNode | InputPort;
export type Predicate = (...inputs: never[]) => Awaitable<boolean>;
export type Condition =
  Predicate | readonly [Predicate, ResultSource, ...ResultSource[]];
export interface NodeOptions {
  readonly label?: string;
  readonly condition?: Condition;
}

function snapshotCondition(
  condition: Condition | undefined,
): Condition | undefined {
  return condition === undefined || typeof condition === "function"
    ? condition
    : (Object.freeze([...condition]) as Condition);
}

/** An actual action node; construction does not execute its callback. */
export class Step<
  T,
  Inputs extends readonly unknown[] = [],
> implements StepNode<T> {
  readonly kind = "step";
  readonly id = native.allocateNodeId();
  readonly label: string;
  readonly condition: Condition | undefined;
  declare readonly [resultValueType]: T;
  readonly #action: (
    context: NodeRunContext,
    ...inputs: OptionInputs<Inputs>
  ) => Awaitable<T>;

  constructor(
    action: (
      context: NodeRunContext,
      ...inputs: OptionInputs<Inputs>
    ) => Awaitable<T>,
    options: NodeOptions = {},
  ) {
    this.#action = action;
    this.label = options.label ?? "step";
    this.condition = snapshotCondition(options.condition);
    Object.freeze(this);
  }
  async [invokeAction](
    context: NodeRunContext,
    inputs: readonly Option<unknown>[],
  ): Promise<unknown> {
    // 异构图只在调用边界擦除参数类型；值本身不复制、不序列化。
    return this.#action(context, ...(inputs as OptionInputs<Inputs>));
  }
}

export interface GraphOptions extends NodeOptions {
  readonly nodes: readonly Node[];
  readonly edges?: readonly Edge[];
  readonly interface?: GraphInterface;
}

/** A complete graph declaration with identity, topology and public ports. */
export class Graph {
  readonly kind = "graph";
  readonly id = native.allocateNodeId();
  readonly label: string;
  readonly condition: Condition | undefined;
  readonly nodes: readonly Node[];
  readonly edges: readonly Edge[];
  readonly interface: GraphInterfaceView;
  constructor(options: GraphOptions) {
    if (new Set(options.nodes).size !== options.nodes.length) {
      throw new Error("a node may occur only once in one Graph");
    }
    this.nodes = Object.freeze([...options.nodes]);
    this.edges = Object.freeze([...(options.edges ?? [])]);
    this.label = options.label ?? "graph";
    this.condition = snapshotCondition(options.condition);
    this.interface = new GraphInterfaceView(
      this,
      options.interface ?? new GraphInterface(),
    );
    Object.freeze(this);
  }
}

export class Results {
  readonly sources: readonly [ResultSource, ...ResultSource[]];
  constructor(sources: readonly [ResultSource, ...ResultSource[]]) {
    if (sources.length === 0)
      throw new Error("result sources must not be empty");
    this.sources = Object.freeze([...sources]) as readonly [
      ResultSource,
      ...ResultSource[],
    ];
    Object.freeze(this);
  }
}

export type EdgeDeclaration =
  | {
      readonly kind: "order";
      readonly source: Node | OutputPort;
      readonly target: Node | InputPort;
      readonly condition: Condition | undefined;
    }
  | {
      readonly kind: "results";
      readonly sources: readonly ResultSource[];
      readonly target: ResultTarget;
    };

/** Completion/readiness or ordered results; result connections also impose waiting. */
export class Edge {
  readonly [edgeDeclaration]: EdgeDeclaration;
  constructor(
    source: Node,
    target: Node,
    options?: { readonly condition?: Condition },
  );
  constructor(
    source: OutputPort,
    target: InputPort,
    options?: { readonly condition?: Condition },
  );
  constructor(source: Results, target: ResultTarget);
  constructor(
    source: Node | OutputPort | Results,
    target: Node | InputPort,
    options: { readonly condition?: Condition } = {},
  ) {
    if (source instanceof Results) {
      if (options.condition !== undefined)
        throw new Error("result edges do not accept a separate edge condition");
      if (target instanceof Graph)
        throw new Error("results require a Step or input port receiver");
      this[edgeDeclaration] = Object.freeze({
        kind: "results",
        sources: source.sources,
        target,
      });
    } else {
      if (source instanceof OutputPort !== target instanceof InputPort) {
        throw new Error(
          "readiness connections require both an output and an input port",
        );
      }
      this[edgeDeclaration] = Object.freeze({
        kind: "order",
        source,
        target,
        condition: snapshotCondition(options.condition),
      });
    }
    Object.freeze(this);
  }
  static results(
    sources: readonly [ResultSource, ...ResultSource[]],
    target: ResultTarget,
  ): Edge {
    return new Edge(new Results(sources), target);
  }
}
