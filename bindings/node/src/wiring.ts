/** Immutable per-node wiring lowered into ordinary graph edges. */
import {
  Edge,
  Results,
  snapshotCondition,
  type Condition,
  type ConditionDeclaration,
  type Node,
  type SourceValues,
  type GraphOptions,
  type ResultSource,
  type ResultSources,
  type StepNode,
} from "./model.js";
import {
  OutputPort,
  type AnyGraphInterface,
  type GraphInterface,
} from "./interface.js";

export class ConditionalPrerequisite<
  const Sources extends ResultSources = ResultSources,
> {
  readonly kind = "conditional";
  readonly condition: ConditionDeclaration;
  constructor(
    readonly node: Node,
    condition: Condition<Sources>,
  ) {
    this.condition = snapshotCondition(condition)!;
    Object.freeze(this);
  }
}
export type OrderPrerequisite = Node | ConditionalPrerequisite;
export type Prerequisite = OrderPrerequisite | Results;
type ResultValues<A extends readonly Prerequisite[]> = A extends readonly [
  infer H,
  ...infer R extends readonly Prerequisite[],
]
  ? H extends Results<infer S>
    ? [...SourceValues<S>, ...ResultValues<R>]
    : ResultValues<R>
  : [];
export type CheckedAfter<
  Inputs extends readonly unknown[],
  A extends readonly Prerequisite[],
> =
  Extract<A[number], Results> extends never
    ? A
    : number extends A["length"]
      ? never
      : ResultValues<A> extends Inputs
        ? A
        : never;
export type WiringGraphOptions<
  I extends AnyGraphInterface = GraphInterface,
  Sources extends ResultSources = ResultSources,
> = Omit<GraphOptions<I, Sources>, "nodes">;
export type WiringGraphArgs<
  I extends AnyGraphInterface,
  Sources extends ResultSources = ResultSources,
> = I extends GraphInterface
  ? [options?: WiringGraphOptions<I, Sources>]
  : [options: WiringGraphOptions<I, Sources>];

export const createNodeWire = Symbol("createNodeWire");
const wireKey = Symbol("wireKey");
export class NodeWiring {
  readonly after: readonly Prerequisite[];
  private constructor(
    readonly node: Node,
    after: readonly Prerequisite[],
    key: symbol,
  ) {
    if (key !== wireKey) throw new Error("obtain node wiring from node.wire()");
    this.after = Object.freeze([...after]);
    Object.freeze(this);
  }
  static [createNodeWire](
    node: Node,
    after: readonly Prerequisite[],
  ): NodeWiring {
    return new NodeWiring(node, after, wireKey);
  }
}

/** Normalize explicit declarations, retaining one complete ordered result group. */
export function normalizeWiring(input: readonly NodeWiring[]): {
  readonly nodes: readonly Node[];
  readonly edges: readonly Edge[];
} {
  const nodes = input.map((wire) => wire.node);
  const known = new Set(nodes);
  const edges: Edge[] = [];
  for (const wire of input) {
    const sources: ResultSource[] = [];
    for (const prerequisite of wire.after) {
      if (prerequisite instanceof Results) {
        if (wire.node.kind !== "step")
          throw new Error(
            "wire Results require a Step receiver; use a Graph input port for graph results",
          );
        for (const source of prerequisite.sources) {
          const owner = source instanceof OutputPort ? source.owner : source;
          if (!known.has(owner))
            throw new Error(
              "result source must be explicitly declared in this wiring",
            );
          sources.push(source);
        }
      } else {
        const source =
          prerequisite instanceof ConditionalPrerequisite
            ? prerequisite.node
            : prerequisite;
        if (!known.has(source))
          throw new Error(
            "prerequisite must be explicitly declared in this wiring",
          );
        // 条件已在 ConditionalPrerequisite 的构造边界检查，异构列表只擦除类型。
        edges.push(
          new Edge(
            source,
            wire.node,
            prerequisite instanceof ConditionalPrerequisite
              ? { condition: prerequisite.condition as Condition }
              : {},
          ),
        );
      }
    }
    if (sources.length !== 0) {
      // 类型在 Step.wire 的声明边界校验；此处只擦除异构接线，不改变顺序。
      const group: ResultSources = [sources[0]!, ...sources.slice(1)];
      edges.push(
        new Edge(
          new Results(group),
          wire.node as StepNode<unknown, SourceValues<typeof group>>,
        ),
      );
    }
  }
  return { nodes, edges };
}
