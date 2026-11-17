//! Language-independent inspection documents and pure structural renderers.

mod mermaid;
mod presentation;
mod schema;
mod tree;
mod validate;
pub use mermaid::render_mermaid;
pub use schema::*;
pub use tree::render_tree;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DocumentError(pub String);
impl std::fmt::Display for DocumentError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for DocumentError {}
impl From<serde_json::Error> for DocumentError {
    fn from(error: serde_json::Error) -> Self {
        Self(error.to_string())
    }
}

/// Canonicalizes an inspection payload without executing or reconstructing a graph.
///
/// # Errors
/// Rejects unsupported schemas, malformed fields and inconsistent references.
pub fn from_value(value: serde_json::Value) -> Result<GraphDocument, DocumentError> {
    let document: GraphDocument = serde_json::from_value(value)?;
    document.validate()?;
    Ok(document)
}
/// Serializes a validated document into JSON-compatible values.
///
/// # Errors
/// Rejects inconsistent structural references or serialization errors.
pub fn to_value(document: &GraphDocument) -> Result<serde_json::Value, DocumentError> {
    document.validate()?;
    Ok(serde_json::to_value(document)?)
}
/// Serializes a validated document as pretty JSON.
///
/// # Errors
/// Rejects inconsistent structural references or serialization errors.
pub fn to_json(document: &GraphDocument) -> Result<String, DocumentError> {
    document.validate()?;
    Ok(serde_json::to_string_pretty(document)?)
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderOptions {
    pub root_label: Option<String>,
    #[serde(default)]
    pub condition_labels: std::collections::BTreeMap<String, String>,
}
