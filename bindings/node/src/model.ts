import { native } from "./backend.js";
import {
  GraphInterface,
  GraphInterfaceView,
  createInterfaceView,
  InputPort,
  OutputPort,
  type AnyGraphInterface,
} from "./interface.js";
import type { Option } from "./option.js";
import type { SharedAccess } from "./shared.js";
import { snapshotStatic, type StaticRule } from "./static.js";
import {
  NodeWiring,
  createNodeWire,
  normalizeWiring,
  type CheckedAfter,
  type Prerequisite,
  type OrderPrerequisite,
  type WiringGraphArgs,
  type WiringGraphOptions,
} from "./wiring.js";

export const invokeAction = Symbol("invokeAction");
export const edgeDeclaration = Symbol("edgeDeclaration");
export const resultValueType = Symbol("resultValueType");
export const resultInputTypes = Symbol("resultInputTypes");
export type Awaitable<T> = T | PromiseLike<T>;
export interface NodeRunContext {
  readonly shared: SharedAccess;
  readonly signal: AbortSignal;
  readonly nodeId: bigint;
  readonly nodePath: readonly number[];
  readonly labelPath: readonly string[];
}
type OptionInputs<T extends readonly unknown[]> = {
  [K in keyof T]: Option<T[K]>;
};
export interface StepNode<
  T = unknown,
  Inputs extends readonly unknown[] = never,
> {
  wire(): NodeWiring;
  wire<const A extends readonly Prerequisite[]>(options: {
    readonly after: CheckedAfter<Inputs, A>;
  }): NodeWiring;
  readonly kind: "step";
  readonly id: bigint;
  readonly label: string;
  readonly condition: ConditionDeclaration | undefined;
  readonly [resultValueType]: T;
  readonly [resultInputTypes]: (values: Inputs) => void;
  [invokeAction](
    context: NodeRunContext,
    inputs: readonly Option<unknown>[],
  ): Promise<unknown>;
}
export type AnyGraph = Graph<AnyGraphInterface>;
export type Node = StepNode | AnyGraph;
export type ResultSource<T = unknown> = StepNode<T> | OutputPort<T>;
export type ResultTarget = StepNode | InputPort<never>;
export type ResultSources = readonly [ResultSource, ...ResultSource[]];
export type SourceValues<Sources extends readonly ResultSource[]> = {
  -readonly [K in keyof Sources]: Sources[K] extends ResultSource<infer T>
    ? T
    : never;
};
type Receiver<Sources extends readonly ResultSource[]> =
  StepNode<unknown, SourceValues<Sources>> | InputPort<SourceValues<Sources>>;
export type Predicate = (...inputs: never) => Awaitable<boolean>;
/** Erased only after a declaration boundary has checked its ordered inputs. */
export type ConditionDeclaration =
  Predicate | readonly [Predicate, ResultSource, ...ResultSource[]];
export type Condition<Sources extends ResultSources = ResultSources> =
  | (() => Awaitable<boolean>)
  | readonly [
      // 仅从来源推导，不能反向用回调参数扩大来源的类型。
      predicate: (
        ...inputs: NoInfer<OptionInputs<SourceValues<Sources>>>
      ) => Awaitable<boolean>,
      ...sources: Sources,
    ];
export interface NodeOptions<Sources extends ResultSources = ResultSources> {
  readonly label?: string;
  readonly condition?: Condition<Sources>;
}

export function snapshotCondition<
  Sources extends ResultSources = ResultSources,
>(
  condition: ConditionDeclaration | Condition<Sources> | undefined,
): ConditionDeclaration | undefined {
  return condition === undefined || typeof condition === "function"
    ? condition
    : (Object.freeze([...condition]) as ConditionDeclaration);
}

/** An actual action node; construction does not execute its callback. */
export class Step<
  T,
  Inputs extends readonly unknown[] = [],
  const ConditionSources extends ResultSources = ResultSources,
> implements StepNode<T, Inputs> {
  wire(): NodeWiring;
  wire<const A extends readonly Prerequisite[]>(options: {
    readonly after: CheckedAfter<Inputs, A>;
  }): NodeWiring;
  wire(options?: { readonly after: readonly Prerequisite[] }): NodeWiring {
    return NodeWiring[createNodeWire](this, options?.after ?? []);
  }
  readonly kind = "step";
  readonly id = native.allocateNodeId();
  readonly label: string;
  readonly condition: ConditionDeclaration | undefined;
  declare readonly [resultValueType]: T;
  declare readonly [resultInputTypes]: (values: Inputs) => void;
  readonly #action: (
    context: NodeRunContext,
    ...inputs: OptionInputs<Inputs>
  ) => Awaitable<T>;

  constructor(
    action: (
      context: NodeRunContext,
      ...inputs: OptionInputs<Inputs>
    ) => Awaitable<T>,
    options: NodeOptions<ConditionSources> = {},
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

export type GraphOptions<
  I extends AnyGraphInterface = GraphInterface,
  Sources extends ResultSources = ResultSources,
> = NodeOptions<Sources> & {
  readonly static?: readonly StaticRule[];
  readonly nodes: readonly Node[];
  readonly edges?: readonly Edge[];
  readonly interface?: I;
} & (I extends GraphInterface ? {} : { readonly interface: I });

/** A complete graph declaration with identity, topology and public ports. */
export class Graph<
  I extends AnyGraphInterface = GraphInterface,
  const ConditionSources extends ResultSources = ResultSources,
> {
  wire(
    options: { readonly after?: readonly OrderPrerequisite[] } = {},
  ): NodeWiring {
    return NodeWiring[createNodeWire](this, options.after ?? []);
  }
  static fromWiring<
    I extends AnyGraphInterface = GraphInterface,
    const Sources extends ResultSources = ResultSources,
  >(
    input: readonly NodeWiring[],
    ...args: WiringGraphArgs<I, Sources>
  ): Graph<I, Sources> {
    const wiring = normalizeWiring(input);
    const options = args[0] as WiringGraphOptions<I, Sources> | undefined;
    // 条件参数组已要求非空接口提供实际声明；此处仅恢复合并前的泛型关系。
    return new Graph<I, Sources>({
      ...options,
      nodes: wiring.nodes,
      edges: [...wiring.edges, ...(options?.edges ?? [])],
    } as unknown as GraphOptions<I, Sources>);
  }
  readonly static: readonly StaticRule[];
  readonly kind = "graph";
  readonly id = native.allocateNodeId();
  readonly label: string;
  readonly condition: ConditionDeclaration | undefined;
  readonly nodes: readonly Node[];
  readonly edges: readonly Edge[];
  readonly interface: GraphInterfaceView<I>;
  constructor(options: GraphOptions<I, ConditionSources>) {
    this.static = snapshotStatic(options.static ?? []);
    if (new Set(options.nodes).size !== options.nodes.length) {
      throw new Error("a node may occur only once in one Graph");
    }
    this.nodes = Object.freeze([...options.nodes]);
    this.edges = Object.freeze([...(options.edges ?? [])]);
    this.label = options.label ?? "graph";
    this.condition = snapshotCondition(options.condition);
    this.interface = GraphInterfaceView[createInterfaceView](
      this,
      (options.interface ?? new GraphInterface()) as I,
    );
    Object.freeze(this);
  }
}

export class Results<const Sources extends ResultSources = ResultSources> {
  readonly sources: Sources;
  constructor(sources: Sources) {
    if (sources.length === 0)
      throw new Error("result sources must not be empty");
    this.sources = Object.freeze([...sources]) as unknown as Sources;
    Object.freeze(this);
  }
}

export type EdgeDeclaration =
  | {
      readonly kind: "order";
      readonly source: Node | OutputPort;
      readonly target: Node | InputPort<never>;
      readonly condition: ConditionDeclaration | undefined;
    }
  | {
      readonly kind: "results";
      readonly sources: readonly ResultSource[];
      readonly target: ResultTarget;
    };

/** Completion/readiness or ordered results; result connections also impose waiting. */
export class Edge<
  const Sources extends ResultSources = ResultSources,
  const ConditionSources extends ResultSources = ResultSources,
> {
  readonly [edgeDeclaration]: EdgeDeclaration;
  constructor(
    source: Node,
    target: Node,
    options?: { readonly condition?: Condition<ConditionSources> },
  );
  constructor(
    source: OutputPort,
    target: InputPort<never>,
    options?: { readonly condition?: Condition<ConditionSources> },
  );
  constructor(source: Results<Sources>, target: Receiver<NoInfer<Sources>>);
  constructor(
    source: Node | OutputPort | Results,
    target: Node | InputPort<never>,
    options: {
      readonly condition?: ConditionDeclaration | Condition<ConditionSources>;
    } = {},
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
  static results<const Sources extends ResultSources>(
    sources: Sources,
    target: Receiver<NoInfer<Sources>>,
  ): Edge<Sources> {
    return new Edge(new Results(sources), target);
  }
}
