//! Read-only adapters; declaration export never calls the projection compiler.
mod compiled;
mod declaration;
use crate::{Compilation, Graph, GraphError, NodeKind, Step};
use inspection::GraphDocument;

pub trait GraphDocumentSource {
    /// Extracts a detached inspection document.
    ///
    /// # Errors
    /// Reports invalid declaration references without invoking predicates or actions.
    fn export_document(&self) -> Result<GraphDocument, GraphError>;
}
/// Exports a declaration or the exact existing compilation, without executing it.
///
/// # Errors
/// Reports invalid document references; declaration export does not perform DAG compilation.
pub fn export_graph(
    source: &(impl GraphDocumentSource + ?Sized),
) -> Result<GraphDocument, GraphError> {
    source.export_document()
}
impl GraphDocumentSource for Graph {
    fn export_document(&self) -> Result<GraphDocument, GraphError> {
        NodeKind::from(self.clone()).export_document()
    }
}
impl GraphDocumentSource for Step {
    fn export_document(&self) -> Result<GraphDocument, GraphError> {
        NodeKind::from(self.clone()).export_document()
    }
}
impl GraphDocumentSource for NodeKind {
    fn export_document(&self) -> Result<GraphDocument, GraphError> {
        let root = Graph::new("<root scope>", [self.clone()], [])?;
        let document = GraphDocument::Declaration(declaration::Index::new(&root)?.document);
        document.validate()?;
        Ok(document)
    }
}
impl GraphDocumentSource for Compilation {
    fn export_document(&self) -> Result<GraphDocument, GraphError> {
        compiled::export(self)
    }
}
