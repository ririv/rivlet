use inspection::{GraphDocument, RenderOptions, from_value, render_mermaid, render_tree, to_value};
use serde_json::{Value, json};

fn fixture() -> Value {
    serde_json::from_str(include_str!("../../../tests/fixtures/inspection/v3.json")).unwrap()
}

#[test]
fn shared_fixture_preserves_full_u64_identity_anonymous_ports_and_opaque_conditions() {
    let source = fixture();
    let document = from_value(source.clone()).unwrap();
    assert_eq!(to_value(&document).unwrap(), source);
    assert_eq!(
        render_tree(&document, &RenderOptions::default()).unwrap(),
        include_str!("../../../tests/fixtures/inspection/v3.tree").trim_end()
    );
    assert_eq!(
        render_mermaid(&document, &RenderOptions::default()).unwrap(),
        include_str!("../../../tests/fixtures/inspection/v3.mmd").trim_end()
    );
}

#[test]
fn external_document_validation_rejects_unknown_schema_and_dangling_references() {
    let mut source = fixture();
    source["schema_version"] = json!(2);
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("expected version 3")
    );
    let mut source = fixture();
    source["nodes"][0]["node_id"] = json!(u64::MAX);
    assert!(from_value(source).is_err());
    let mut source = fixture();
    source["edges"][0]["source"] = json!("missing");
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("unknown node")
    );
    let mut source = fixture();
    source["edges"][1]["provider_node"] = json!("0.1");
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("provider disagree")
    );
    let mut source = fixture();
    source["nodes"][4]["condition"] = json!("missing");
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("unknown node condition")
    );
}

#[test]
fn empty_root_and_empty_child_are_structural_not_fake_actions() {
    let document = from_value(json!({"schema_version":3,"view":"declaration","root":"empty","parent":null,"graphs":[{"id":"empty","label":"empty","label_path":[],"owner":null,"nodes":[],"ports":[]}],"nodes":[],"edges":[],"conditions":[],"static_rules":[],"selections":[]})).unwrap();
    assert_eq!(
        render_tree(&document, &RenderOptions::default()).unwrap(),
        "empty"
    );
    assert!(
        render_mermaid(&document, &RenderOptions::default())
            .unwrap()
            .contains("[\"empty\"]")
    );
    assert!(matches!(document, GraphDocument::Declaration(_)));
}

#[test]
fn wire_boundary_distinguishes_anonymous_port_from_no_port() {
    let mut source = fixture();
    source["edges"][1]
        .as_object_mut()
        .unwrap()
        .remove("source_port");
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("declared together")
    );
}

#[test]
fn external_parent_cannot_form_an_ownership_loop_into_its_own_document() {
    let mut source = fixture();
    source["parent"] = json!("0");
    source["graphs"][0]["owner"] = json!("0");
    assert!(
        from_value(source)
            .unwrap_err()
            .to_string()
            .contains("outside the exported scope")
    );
}
