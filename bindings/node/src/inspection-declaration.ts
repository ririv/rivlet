import {
  edgeDeclaration,
  type AnyGraph,
  type ConditionDeclaration,
  type Node,
  type ResultSource,
} from "./model.js";
import {
  InputPort,
  OutputPort,
  type InputEndpoint,
  type OutputEndpoint,
} from "./interface.js";
import type {
  DeclarationDocument,
  EdgeDocument,
  NodeDocument,
  ScopeDocument,
  StaticDocument,
  SelectionDocument,
  ConditionDocument,
} from "./document.js";

export const pathId = (path: readonly number[]): string => path.join(".");
export const scopeId = (owner: string | null): string =>
  owner === null ? "graph:root" : `graph:${owner}`;
interface Scope {
  readonly graph: AnyGraph | undefined;
  readonly owner: string | null;
  readonly local: ReadonlyMap<Node, string>;
  readonly children: Map<Node, number>;
}
export interface EdgeOrigin {
  readonly document: number;
  readonly owner: string;
  readonly edge: number;
}
function need<T>(value: T | undefined): T {
  if (value === undefined)
    throw new Error("inspection endpoint is outside its declared scope");
  return value;
}

/** Raw declaration traversal: no compilation or predicate evaluation. */
export class DeclarationIndex {
  readonly document: DeclarationDocument;
  readonly origins: EdgeOrigin[] = [];
  readonly #scopes: Scope[] = [];
  readonly #nodes: NodeDocument[] = [];
  readonly #graphs: ScopeDocument[] = [];
  readonly #edges: EdgeDocument[] = [];
  readonly #conditions: ConditionDocument[] = [];
  readonly #static: StaticDocument[] = [];
  readonly #selections: SelectionDocument[] = [];
  readonly #pending: {
    scope: number;
    id: string;
    condition: ConditionDeclaration;
  }[] = [];

  constructor(root: Node) {
    this.#visit(undefined, null, [root], [], []);
    for (let scope = 0; scope < this.#scopes.length; scope++) this.#fill(scope);
    for (const { scope, id, condition } of this.#pending) {
      const sources =
        typeof condition === "function"
          ? []
          : (condition.slice(1) as ResultSource[]);
      this.#conditions.push({
        id,
        inputs: sources.map((source) => this.#output(scope, source)),
      });
    }
    this.document = {
      schema_version: 3,
      view: "declaration",
      root: "graph:root",
      parent: null,
      graphs: this.#graphs,
      nodes: this.#nodes,
      edges: this.#edges,
      conditions: this.#conditions,
      static_rules: this.#static,
      selections: this.#selections,
    };
  }
  #visit(
    graph: AnyGraph | undefined,
    owner: string | null,
    nodes: readonly Node[],
    path: readonly number[],
    labels: readonly string[],
  ): number {
    const index = this.#scopes.length;
    const id = scopeId(owner);
    const locals = new Map(
      nodes.map((node, position) => [
        node,
        pathId(graph === undefined ? [] : [...path, position]),
      ]),
    );
    this.#scopes.push({ graph, owner, local: locals, children: new Map() });
    this.#graphs.push({
      id,
      owner,
      label: graph?.label ?? need(nodes[0]).label,
      label_path: [...labels],
      nodes: [...locals.values()],
      ports: [],
    });
    nodes.forEach((node, position) => {
      const mounted = need(locals.get(node));
      const childLabels = [...labels, node.label];
      const metadata = {
        id: mounted,
        node_id: String(node.id),
        graph: id,
        label: node.label,
        label_path: childLabels,
        condition:
          node.condition === undefined
            ? null
            : this.#condition(index, mounted, node.condition),
      };
      if (node.kind === "step")
        this.#nodes.push({
          ...metadata,
          kind: "leaf",
          action_kind: "javascript",
        });
      else {
        this.#nodes.push({
          ...metadata,
          kind: "subgraph",
          child_graph: scopeId(mounted),
        });
        const child = this.#visit(
          node,
          mounted,
          node.nodes,
          graph === undefined ? [] : [...path, position],
          childLabels,
        );
        need(this.#scopes[index]).children.set(node, child);
      }
    });
    return index;
  }
  #local(scope: number, node: Node): string {
    return need(need(this.#scopes[scope]).local.get(node));
  }
  #child(scope: number, node: Node): number {
    return need(need(this.#scopes[scope]).children.get(node));
  }
  #output(scope: number, endpoint: OutputEndpoint): string {
    if (!(endpoint instanceof OutputPort)) return this.#local(scope, endpoint);
    return this.#output(
      this.#child(scope, endpoint.owner),
      endpoint.owner.interface.declaration.output(endpoint.name),
    );
  }
  #inputs(scope: number, endpoint: InputEndpoint): string[] {
    if (!(endpoint instanceof InputPort)) return [this.#local(scope, endpoint)];
    const child = this.#child(scope, endpoint.owner);
    return endpoint.owner.interface.declaration
      .input(endpoint.name)
      .flatMap((target) => this.#inputs(child, target));
  }
  #condition(
    scope: number,
    target: string,
    condition: ConditionDeclaration,
  ): string {
    const id = `${target}/condition:${this.#pending.length}`;
    this.#pending.push({ scope, id, condition });
    return id;
  }
  #fill(scope: number): void {
    const current = need(this.#scopes[scope]);
    const graph = current.graph;
    if (graph === undefined) return;
    const id = scopeId(current.owner);
    this.#graphs[scope] = {
      ...need(this.#graphs[scope]),
      ports: [
        ...graph.interface.declaration
          .inputEntries()
          .map(([port, targets]) => ({
            kind: "input" as const,
            port,
            nodes: targets.flatMap((target) => this.#inputs(scope, target)),
          })),
        ...graph.interface.declaration
          .outputEntries()
          .map(([port, target]) => ({
            kind: "output" as const,
            port,
            node: this.#output(scope, target),
          })),
      ],
    };
    graph.edges.forEach((edge, position) => {
      const spec = edge[edgeDeclaration];
      const base = `${id}/edge:${position}`;
      const target = this.#local(
        scope,
        spec.target instanceof InputPort ? spec.target.owner : spec.target,
      );
      const condition =
        spec.kind === "order" && spec.condition !== undefined
          ? this.#condition(scope, target, spec.condition)
          : null;
      const emit = (document: EdgeDocument): void => {
        this.origins.push({
          document: this.#edges.length,
          owner: need(current.owner ?? undefined),
          edge: position,
        });
        this.#edges.push(document);
      };
      if (spec.kind === "results") {
        spec.sources.forEach((source, argument_index) =>
          emit({
            id: `${base}/input:${argument_index}`,
            graph: id,
            kind: "result",
            source: this.#local(
              scope,
              source instanceof OutputPort ? source.owner : source,
            ),
            target,
            argument_index,
            ...(source instanceof OutputPort
              ? {
                  source_port: source.name,
                  provider_node: this.#output(scope, source),
                }
              : {}),
            ...(spec.target instanceof InputPort
              ? {
                  target_port: spec.target.name,
                  consumer_nodes: this.#inputs(scope, spec.target),
                }
              : {}),
          }),
        );
      } else if (
        spec.source instanceof OutputPort &&
        spec.target instanceof InputPort
      ) {
        emit({
          id: base,
          graph: id,
          kind: "port",
          source: this.#local(scope, spec.source.owner),
          target,
          source_port: spec.source.name,
          target_port: spec.target.name,
          provider_node: this.#output(scope, spec.source),
          consumer_nodes: this.#inputs(scope, spec.target),
          condition,
        });
      } else {
        const metadata = {
          id: base,
          graph: id,
          source: this.#local(scope, spec.source as Node),
          target,
        };
        emit(
          condition === null
            ? { ...metadata, kind: "order" }
            : { ...metadata, kind: "conditional", condition },
        );
      }
    });
    graph.static.forEach((rule, position) => {
      const condition = `${id}/static:${position}`;
      if (rule.kind === "node")
        this.#static.push({
          kind: "node",
          node: this.#local(scope, rule.node),
          condition,
        });
      else if (rule.kind === "edge")
        this.#static.push({
          kind: "edge",
          source: this.#local(scope, rule.source),
          target: this.#local(scope, rule.target),
          condition,
        });
      else
        this.#selections.push({
          id: `${id}/selection:${position}`,
          cases: rule.cases.map((item, index) => ({
            node: this.#local(scope, item.node),
            condition:
              typeof item.condition === "boolean"
                ? item.condition
                : `${condition}/case:${index}`,
          })),
        });
    });
  }
}
