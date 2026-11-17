import { native } from "./backend.js";
import { Compilation, inspectionFacts } from "./compilation.js";
import type { Node } from "./model.js";
import type {
  GraphDocument,
  DeclarationDocument,
  CompilationDocument,
  EdgeDocument,
  DependencyDocument,
  PortDocument,
} from "./document.js";
import { DeclarationIndex, pathId } from "./inspection-declaration.js";

function compiledDocument(compilation: Compilation): CompilationDocument {
  const index = new DeclarationIndex(compilation.root);
  const declaration = index.document;
  const active = new Map(
    compilation.nodes.map((node) => [pathId(node.nodePath), node]),
  );
  const byIndex = (index: number): string => {
    const node = compilation.nodes[index];
    if (node === undefined) throw new Error("missing compiled inspection node");
    return pathId(node.nodePath);
  };
  const facts = compilation[inspectionFacts]();
  const nodeConditions = new Map<string, string>();
  const edgeConditions = new Map<string, string>();
  const conditions = compilation.conditions.map((condition, index) => {
    const id = `condition:${index}`;
    const site = condition.site;
    if (site.kind === "node") nodeConditions.set(byIndex(site.target), id);
    else edgeConditions.set(`${byIndex(site.owner)}/edge:${site.edge}`, id);
    return { id, index, inputs: condition.inputs.map(byIndex) };
  });
  const disabled = new Set(
    facts.disabledEdges.map(
      ([owner, edge]) => `${byIndex(owner)}/edge:${edge}`,
    ),
  );
  const edges: EdgeDocument[] = [];
  for (const origin of index.origins) {
    const edge = declaration.edges[origin.document]!;
    const site = `${origin.owner}/edge:${origin.edge}`;
    if (
      !active.has(edge.source) ||
      !active.has(edge.target) ||
      disabled.has(site)
    )
      continue;
    if (edge.kind === "conditional") {
      const condition = edgeConditions.get(site);
      if (condition === undefined)
        throw new Error("missing compiled edge condition");
      edges.push({ ...edge, condition });
    } else if (
      edge.kind === "port" ||
      (edge.kind === "result" && edge.consumer_nodes !== undefined)
    ) {
      const consumer_nodes = edge.consumer_nodes!.filter((node) =>
        active.has(node),
      );
      if (consumer_nodes.length === 0) continue;
      if (edge.kind === "port") {
        const condition =
          edge.condition === null ? null : edgeConditions.get(site);
        if (condition === undefined)
          throw new Error("missing compiled port condition");
        edges.push({ ...edge, consumer_nodes, condition });
      } else edges.push({ ...edge, consumer_nodes });
    } else edges.push(edge);
  }
  const retained = new Set(edges.map((edge) => edge.id));
  const dependencies: DependencyDocument[] = facts.dependencies.map((dep) =>
    dep.kind === "required"
      ? { ...dep, source: byIndex(dep.source), target: byIndex(dep.target) }
      : {
          ...dep,
          source: byIndex(dep.source),
          target: byIndex(dep.target),
          condition: `condition:${dep.condition}`,
        },
  );
  return {
    schema_version: 3,
    view: "compiled",
    root: declaration.root,
    parent: declaration.parent,
    nodes: declaration.nodes
      .filter((node) => active.has(node.id))
      .map((node) => ({
        ...node,
        condition: nodeConditions.get(node.id) ?? null,
      })),
    graphs: declaration.graphs
      .filter((scope) => scope.owner === null || active.has(scope.owner))
      .map((scope) => ({
        ...scope,
        nodes: scope.nodes.filter((node) => active.has(node)),
        ports: scope.ports.flatMap<PortDocument>((port) => {
          if (port.kind === "output")
            return active.has(port.node) ? [port] : [];
          const nodes = port.nodes.filter((node) => active.has(node));
          return nodes.length === 0 ? [] : [{ ...port, nodes }];
        }),
      })),
    edges,
    conditions,
    dependencies,
    excluded_nodes: declaration.nodes
      .filter((node) => !active.has(node.id))
      .map((node) => node.id),
    excluded_edges: declaration.edges
      .filter((edge) => !retained.has(edge.id))
      .map((edge) => edge.id),
  };
}

/** Export a detached JSON-compatible document; no callback is invoked. */
export function exportGraph(source: Compilation): CompilationDocument;
export function exportGraph(source: Node): DeclarationDocument;
export function exportGraph(source: Node | Compilation): GraphDocument;
export function exportGraph(source: Node | Compilation): GraphDocument {
  const document =
    source instanceof Compilation
      ? compiledDocument(source)
      : new DeclarationIndex(source).document;
  return native.validateGraphDocument(document) as GraphDocument;
}
export interface RenderOptions {
  readonly rootLabel?: string;
}
type RenderSubject = Node | Compilation | GraphDocument;
function render(
  source: RenderSubject,
  format: "tree" | "mermaid",
  options: RenderOptions,
): string {
  const document = "view" in source ? source : exportGraph(source);
  return native.renderGraphDocument(document, format, {
    root_label: options.rootLabel ?? null,
    condition_labels: {},
  });
}
export function renderTree(
  source: RenderSubject,
  options: RenderOptions = {},
): string {
  return render(source, "tree", options);
}
export function renderMermaid(
  source: RenderSubject,
  options: RenderOptions = {},
): string {
  return render(source, "mermaid", options);
}
