//! Thin Node-API boundary for the shared read-only graph document.
use napi::bindgen_prelude::{Error, Result, Status};
use napi_derive::napi;
use serde_json::Value;

fn invalid(error: impl std::fmt::Display) -> Error {
    Error::new(Status::InvalidArg, error.to_string())
}

/// Validates and returns a detached canonical document.
///
/// # Errors
/// Rejects unsupported schemas and invalid structural references.
#[napi(ts_args_type = "document: unknown", ts_return_type = "unknown")]
pub fn validate_graph_document(document: Value) -> Result<Value> {
    let document = inspection::from_value(document).map_err(invalid)?;
    inspection::to_value(&document).map_err(invalid)
}

/// Renders a document without compiling it or invoking any application callback.
///
/// # Errors
/// Rejects invalid documents, options or render formats.
#[napi(ts_args_type = "document: unknown, format: string, options: unknown")]
#[allow(clippy::needless_pass_by_value, reason = "Node-API 字符串参数必须按值接收")]
pub fn render_graph_document(document: Value, format: String, options: Value) -> Result<String> {
    let document = inspection::from_value(document).map_err(invalid)?;
    let options = serde_json::from_value(options).map_err(invalid)?;
    match format.as_str() {
        "tree" => inspection::render_tree(&document, &options).map_err(invalid),
        "mermaid" => inspection::render_mermaid(&document, &options).map_err(invalid),
        _ => Err(invalid("unknown graph render format")),
    }
}
