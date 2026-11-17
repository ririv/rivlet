import { native } from "./backend.js";
import {
  edgeDeclaration,
  type Condition,
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
  Declaration,
  NativeCompilation,
  NativeSession,
  SessionOptions,
} from "./protocol.js";
import { staticBindings } from "./static.js";

export const createSession = Symbol("createSession");

export interface MountedNode {
  readonly index: number;
  readonly node: Node;
  readonly nodePath: readonly number[];
  readonly labelPath: readonly string[];
  readonly owner: number | null;
  readonly inputs: readonly number[];
}
interface RecordDraft {
  readonly index: number;
  readonly node: Node;
  readonly scope: Scope;
  readonly nodePath: readonly number[];
  readonly labelPath: readonly string[];
  inputs: readonly number[] | undefined;
  child: Scope | undefined;
}
interface Scope {
  readonly id: number;
  readonly owner: RecordDraft | undefined;
  readonly local: Map<Node, RecordDraft>;
  readonly declared: ReadonlySet<Node>;
  readonly staticEdges?:
    ReadonlyMap<import("./model.js").Edge, boolean> | undefined;
}
export interface BoundCondition {
  readonly predicate: (...args: never[]) => boolean | PromiseLike<boolean>;
  readonly inputs: readonly number[];
}

function requireValue<T>(value: T | undefined, message: string): T {
  if (value === undefined) throw new Error(message);
  return value;
}

/** One immutable declaration compilation; execution never recompiles it. */
export class Compilation {
  readonly nodes: readonly MountedNode[];
  readonly conditions: readonly BoundCondition[];
  readonly #backend: NativeCompilation;
  readonly #scopes: Map<number, Scope>;
  readonly #mounts: ReadonlyMap<Node, readonly number[]>;

  constructor(readonly root: Node) {
    const declaration: Declaration = {
      graphs: [{ kind: "root" }],
      nodes: [],
      conditions: [],
      ports: [],
    };
    const records: RecordDraft[] = [];
    const scopes: Scope[] = [
      { id: 0, owner: undefined, local: new Map(), declared: new Set([root]) },
    ];
    const drafts: BoundCondition[] = [];
    const visit = (
      node: Node,
      scope: Scope,
      path: readonly number[],
      labels: readonly string[],
    ): RecordDraft => {
      const index = records.length;
      const record: RecordDraft = {
        index,
        node,
        scope,
        nodePath: Object.freeze([...path]),
        labelPath: Object.freeze([...labels, node.label]),
        inputs: undefined,
        child: undefined,
      };
      records.push(record);
      scope.local.set(node, record);
      const activation = {
        kind: "active" as const,
        prerequisites: [],
        condition: null,
      };
      if (node.kind === "step") {
        declaration.nodes.push({ kind: "step", graph: scope.id, activation });
      } else {
        const bindings = staticBindings(node);
        const child: Scope = {
          id: scopes.length,
          owner: record,
          local: new Map(),
          declared: new Set(node.nodes),
          staticEdges: bindings?.edges,
        };
        for (const edge of node.edges) {
          const spec = edge[edgeDeclaration];
          const target = spec.target instanceof InputPort ? spec.target.owner : spec.target;
          if (!child.declared.has(target)) throw new Error("edge target is outside its graph");
          const sources = spec.kind === "results" ? spec.sources : [spec.source];
          for (const endpoint of sources) {
            const source = endpoint instanceof OutputPort ? endpoint.owner : endpoint;
            if (!child.declared.has(source)) throw new Error("edge source is outside its graph");
          }
        }
        record.child = child;
        scopes.push(child);
        declaration.graphs.push({ kind: "owned", node: index });
        declaration.nodes.push({
          kind: "graph",
          graph: scope.id,
          childGraph: child.id,
          activation,
        });
        node.nodes.forEach((item, position) => {
          if (bindings?.nodes.get(item) !== false)
            visit(item, child, [...path, position], record.labelPath);
        });
      }
      return record;
    };
    visit(root, requireValue(scopes[0], "missing root scope"), [], []);
    this.#scopes = new Map(
      records.flatMap((record) =>
        record.child === undefined ? [] : [[record.index, record.child]],
      ),
    );

    const local = (node: Node, scope: Scope): RecordDraft =>
      requireValue(
        scope.local.get(node),
        "endpoint must be a direct node of this scope; use a child Graph port",
      );
    const output = (endpoint: OutputEndpoint, scope: Scope): number => {
      if (!(endpoint instanceof OutputPort))
        return local(endpoint, scope).index;
      const owner = local(endpoint.owner, scope);
      const child = requireValue(
        owner.child,
        "output port owner has no child scope",
      );
      return output(
        endpoint.owner.interface.declaration.output(endpoint.name),
        child,
      );
    };
    const inputs = (endpoint: InputEndpoint, scope: Scope): number[] => {
      if (!(endpoint instanceof InputPort))
        return [local(endpoint, scope).index];
      const owner = local(endpoint.owner, scope);
      const child = requireValue(
        owner.child,
        "input port owner has no child scope",
      );
      return endpoint.owner.interface.declaration
        .input(endpoint.name)
        .flatMap((item) => inputs(item, child));
    };
    const condition = (value: Condition, scope: Scope): number => {
      const predicate = typeof value === "function" ? value : value[0];
      const sources =
        typeof value === "function" ? [] : (value.slice(1) as ResultSource[]);
      const reads = sources.map((source) => output(source, scope));
      const index = drafts.length;
      drafts.push({ predicate, inputs: Object.freeze(reads) });
      declaration.conditions.push(reads);
      return index;
    };
    for (const record of records) {
      const spec = requireValue(
        declaration.nodes[record.index],
        "missing declaration",
      );
      if (spec.activation.kind !== "active")
        throw new Error("unexpected inactive JS declaration");
      if (record.node.condition !== undefined)
        spec.activation.condition = condition(
          record.node.condition,
          record.scope,
        );
      if (record.node.kind !== "graph") continue;
      const scope = requireValue(record.child, "graph has no scope");
      for (const edge of record.node.edges) {
        if (scope.staticEdges?.get(edge) === false) continue;
        const edgeSpec = edge[edgeDeclaration];
        const receiver =
          edgeSpec.target instanceof InputPort
            ? edgeSpec.target.owner
            : edgeSpec.target;
        if (!scope.local.has(receiver)) continue;
        if (edgeSpec.kind === "results") {
          const reads = edgeSpec.sources.map((source) => output(source, scope));
          for (const receiver of inputs(edgeSpec.target, scope)) {
            const target = requireValue(
              records[receiver],
              "unknown result receiver",
            );
            if (target.node.kind !== "step")
              throw new Error("result input must resolve to a Step");
            if (target.inputs !== undefined)
              throw new Error(
                "a Step must receive exactly one complete result group",
              );
            target.inputs = Object.freeze(reads);
            const receiverSpec = requireValue(
              declaration.nodes[receiver],
              "unknown receiver declaration",
            );
            if (receiverSpec.activation.kind !== "active")
              throw new Error("unexpected inactive receiver");
            for (const source of reads)
              receiverSpec.activation.prerequisites.push({
                kind: "required",
                node: source,
              });
          }
        } else {
          const owner =
            edgeSpec.source instanceof OutputPort
              ? edgeSpec.source.owner
              : edgeSpec.source;
          if (!scope.local.has(owner)) continue;
          const source = output(edgeSpec.source, scope);
          const guard =
            edgeSpec.condition === undefined
              ? undefined
              : condition(edgeSpec.condition, scope);
          for (const target of inputs(edgeSpec.target, scope)) {
            if (edgeSpec.source instanceof OutputPort) {
              declaration.ports.push(
                guard === undefined
                  ? { kind: "required", source, target }
                  : { kind: "conditional", source, target, condition: guard },
              );
            } else {
              const targetSpec = requireValue(
                declaration.nodes[target],
                "unknown edge target",
              );
              if (targetSpec.activation.kind !== "active")
                throw new Error("unexpected inactive target");
              targetSpec.activation.prerequisites.push(
                guard === undefined
                  ? { kind: "required", node: source }
                  : { kind: "conditional", node: source, condition: guard },
              );
            }
          }
        }
      }
    }
    this.#backend = native.compileDeclaration(declaration);
    const mapping = this.#backend.mappings();
    // 当前 JS 声明均为 active；core 仍负责全部 DAG 与条件依赖检查。
    if (
      mapping.nodes.some((index, compiled) => index !== compiled) ||
      mapping.nodes.length !== records.length
    ) {
      throw new Error("unexpected JS declaration projection");
    }
    this.nodes = Object.freeze(
      records.map((record) =>
        Object.freeze({
          index: record.index,
          node: record.node,
          nodePath: record.nodePath,
          labelPath: record.labelPath,
          owner: record.scope.owner?.index ?? null,
          inputs: record.inputs ?? Object.freeze([]),
        }),
      ),
    );
    this.conditions = Object.freeze(drafts.map((item) => Object.freeze(item)));
    const mounts = new Map<Node, number[]>();
    for (const record of this.nodes) {
      const indices = mounts.get(record.node);
      if (indices === undefined) mounts.set(record.node, [record.index]);
      else indices.push(record.index);
    }
    this.#mounts = new Map(
      [...mounts].map(([node, indices]) => [node, Object.freeze(indices)]),
    );
    Object.freeze(this);
  }

  /** Resolve a public output by its actual mount, never only by shared identity. */
  resultIndex(source: ResultSource): number {
    if (source instanceof OutputPort) {
      const owner = this.uniqueIndex(source.owner);
      const scope = requireValue(
        this.#scopes.get(owner),
        "output owner has no scope",
      );
      return this.#outputIndex(
        source.owner.interface.declaration.output(source.name),
        scope,
      );
    }
    return this.uniqueIndex(source);
  }
  #outputIndex(source: OutputEndpoint, scope: Scope): number {
    if (!(source instanceof OutputPort))
      return requireValue(
        scope.local.get(source),
        "output endpoint is outside its graph",
      ).index;
    const owner = requireValue(
      scope.local.get(source.owner),
      "forwarded output owner is outside its graph",
    );
    return this.#outputIndex(
      source.owner.interface.declaration.output(source.name),
      requireValue(owner.child, "missing forwarded scope"),
    );
  }
  uniqueIndex(node: Node): number {
    const found = this.#mounts.get(node) ?? [];
    if (found.length !== 1)
      throw new Error(
        "node is absent or has multiple mounts; select a specific Graph output",
      );
    return requireValue(found[0], "missing mounted node");
  }
  [createSession](options: SessionOptions): NativeSession {
    return this.#backend.session(options);
  }
}
