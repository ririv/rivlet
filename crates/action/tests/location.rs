use action::{ActionContext, NodeLocation};
use rivlet::NodeIdentity;

#[test]
fn low_level_hosts_bind_one_complete_location_without_an_implicit_identity() {
    let unbound = ActionContext::default();
    assert!(unbound.node_id().is_err());
    assert!(unbound.node_path().is_err());
    assert!(unbound.label_path().is_err());
    let id = NodeIdentity::allocate();
    let context = unbound.with_location(NodeLocation::new(
        id,
        vec![0, 1],
        vec!["root".into(), "work".into()],
    ));
    let cloned = context.clone();
    assert_eq!(context, cloned);
    assert_eq!(cloned.node_id().unwrap(), id);
    assert_eq!(cloned.node_path().unwrap(), &[0, 1]);
    assert_eq!(cloned.label_path().unwrap(), &["root", "work"]);
}
