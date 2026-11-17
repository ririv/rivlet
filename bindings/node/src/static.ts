import {
  edgeDeclaration,
  type Node,
  type Edge,
  type AnyGraph,
} from "./model.js";
import { InputPort, OutputPort } from "./interface.js";

export type StaticCondition = boolean | (() => boolean);
export class Case {
  constructor(
    readonly condition: StaticCondition,
    readonly node: Node,
  ) {
    Object.freeze(this);
  }
}
export type StaticRule =
  | {
      readonly kind: "node";
      readonly condition: StaticCondition;
      readonly node: Node;
    }
  | {
      readonly kind: "edge";
      readonly condition: StaticCondition;
      readonly source: Node;
      readonly target: Node;
    }
  | { readonly kind: "selection"; readonly cases: readonly Case[] };

export const Static = Object.freeze({
  when(condition: StaticCondition, node: Node): StaticRule {
    return Object.freeze({ kind: "node", condition, node });
  },
  edge(condition: StaticCondition, source: Node, target: Node): StaticRule {
    return Object.freeze({ kind: "edge", condition, source, target });
  },
  select(cases: readonly Case[]): StaticRule {
    return Object.freeze({
      kind: "selection",
      cases: Object.freeze([...cases]),
    });
  },
});

function evaluate(condition: StaticCondition): boolean {
  const value = typeof condition === "function" ? condition() : condition;
  if (typeof value !== "boolean")
    throw new Error("a static condition must return a boolean synchronously");
  return value;
}

export function snapshotStatic(
  rules: readonly StaticRule[],
): readonly StaticRule[] {
  return Object.freeze(
    rules.map((rule) =>
      rule.kind === "selection"
        ? Static.select(
            rule.cases.map((item) => new Case(item.condition, item.node)),
          )
        : Object.freeze({ ...rule }),
    ),
  );
}

/** Evaluate only a graph scope that is actually selected by its ancestors. */
export function staticBindings(
  graph: AnyGraph,
):
  | { nodes: ReadonlyMap<Node, boolean>; edges: ReadonlyMap<Edge, boolean> }
  | undefined {
  if (graph.static.length === 0) return undefined;
  const known = new Set(graph.nodes);
  const nodes = new Map<Node, boolean>();
  const edges = new Map<Edge, boolean>();
  const requireNode = (node: Node): void => {
    if (!known.has(node))
      throw new Error("static rule references a node outside its graph");
  };
  const bind = (node: Node, value: boolean): void => {
    requireNode(node);
    if (nodes.has(node))
      throw new Error("static activation is declared twice for one node");
    nodes.set(node, value);
  };
  for (const rule of graph.static) {
    switch (rule.kind) {
      case "node":
        bind(rule.node, evaluate(rule.condition));
        break;
      case "selection": {
        let selected: Node | undefined;
        for (const item of rule.cases) {
          requireNode(item.node);
          if (evaluate(item.condition)) {
            if (selected !== undefined)
              throw new Error(
                "static selection requires exactly one active case; multiple are active",
              );
            selected = item.node;
          }
        }
        if (selected === undefined)
          throw new Error(
            "static selection requires exactly one active case; none are active",
          );
        for (const item of rule.cases) bind(item.node, item.node === selected);
        break;
      }
      case "edge": {
        requireNode(rule.source);
        requireNode(rule.target);
        const enabled = evaluate(rule.condition);
        let found = false;
        for (const edge of graph.edges) {
          const declaration = edge[edgeDeclaration];
          const target =
            declaration.target instanceof InputPort
              ? declaration.target.owner
              : declaration.target;
          if (target !== rule.target) continue;
          if (declaration.kind === "results") {
            if (
              declaration.sources.some(
                (source) =>
                  (source instanceof OutputPort ? source.owner : source) ===
                  rule.source,
              )
            )
              throw new Error(
                "static edge guards cannot remove a result input; guard the receiving node instead",
              );
            continue;
          }
          const source =
            declaration.source instanceof OutputPort
              ? declaration.source.owner
              : declaration.source;
          if (source !== rule.source) continue;
          if (edges.has(edge))
            throw new Error("static activation is declared twice for one edge");
          edges.set(edge, enabled);
          found = true;
        }
        if (!found) throw new Error("static rule references an unknown edge");
        break;
      }
      default:
        throw new Error("unknown static rule");
    }
  }
  return { nodes, edges };
}
