/** Detached inspection schema, not an executable graph interchange format. */
export interface ScopeDocument {
  readonly id: string;
  readonly label: string;
  readonly label_path: readonly string[];
  readonly owner: string | null;
  readonly nodes: readonly string[];
  readonly ports: readonly PortDocument[];
}
export type PortDocument =
  | {
      readonly kind: "input";
      readonly port: string | null;
      readonly nodes: readonly string[];
    }
  | {
      readonly kind: "output";
      readonly port: string | null;
      readonly node: string;
    };
interface NodeMetadata {
  readonly id: string;
  readonly node_id: string;
  readonly graph: string;
  readonly label: string;
  readonly label_path: readonly string[];
  readonly condition: string | null;
}
export type NodeDocument = NodeMetadata &
  (
    | {
        readonly kind: "leaf";
        readonly action_kind: "python" | "native" | "rust" | "javascript";
      }
    | { readonly kind: "subgraph"; readonly child_graph: string }
  );
interface EdgeMetadata {
  readonly id: string;
  readonly graph: string;
  readonly source: string;
  readonly target: string;
}
type SourceProjection =
  | { readonly source_port?: never; readonly provider_node?: never }
  | { readonly source_port: string | null; readonly provider_node: string };
type TargetProjection =
  | { readonly target_port?: never; readonly consumer_nodes?: never }
  | {
      readonly target_port: string | null;
      readonly consumer_nodes: readonly string[];
    };
export type EdgeDocument = EdgeMetadata &
  (
    | { readonly kind: "order" }
    | { readonly kind: "conditional"; readonly condition: string }
    | ({
        readonly kind: "result";
        readonly argument_index: number;
      } & SourceProjection &
        TargetProjection)
    | {
        readonly kind: "port";
        readonly source_port: string | null;
        readonly target_port: string | null;
        readonly provider_node: string;
        readonly consumer_nodes: readonly string[];
        readonly condition: string | null;
      }
  );
export interface ConditionDocument {
  readonly id: string;
  readonly inputs: readonly string[];
}
export type StaticDocument =
  | { readonly kind: "node"; readonly node: string; readonly condition: string }
  | {
      readonly kind: "edge";
      readonly source: string;
      readonly target: string;
      readonly condition: string;
    };
export interface SelectionDocument {
  readonly id: string;
  readonly cases: readonly {
    readonly node: string;
    readonly condition: boolean | string;
  }[];
}
export type DependencyDocument =
  | {
      readonly kind: "required";
      readonly source: string;
      readonly target: string;
    }
  | {
      readonly kind: "conditional";
      readonly source: string;
      readonly target: string;
      readonly condition: string;
    };
interface DocumentMetadata {
  readonly schema_version: 3;
  readonly root: string;
  readonly parent: string | null;
  readonly graphs: readonly ScopeDocument[];
  readonly nodes: readonly NodeDocument[];
  readonly edges: readonly EdgeDocument[];
}
export interface DeclarationDocument extends DocumentMetadata {
  readonly view: "declaration";
  readonly conditions: readonly ConditionDocument[];
  readonly static_rules: readonly StaticDocument[];
  readonly selections: readonly SelectionDocument[];
}
export interface CompilationDocument extends DocumentMetadata {
  readonly view: "compiled";
  readonly conditions: readonly (ConditionDocument & {
    readonly index: number;
  })[];
  readonly dependencies: readonly DependencyDocument[];
  readonly excluded_nodes: readonly string[];
  readonly excluded_edges: readonly string[];
}
export type GraphDocument = DeclarationDocument | CompilationDocument;
