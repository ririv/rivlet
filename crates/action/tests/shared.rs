use action::{ActionContext, SharedStateError};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[test]
fn cloned_contexts_access_the_same_checked_object_without_copying() {
    let progress = Arc::new(AtomicUsize::new(0));
    let context = ActionContext::default().with_shared(Arc::clone(&progress));
    let cloned = context.clone();
    assert_eq!(context, cloned);
    let state = cloned.shared::<AtomicUsize>().unwrap();
    assert!(Arc::ptr_eq(&state, &progress));
    state.fetch_add(1, Ordering::SeqCst);
    assert_eq!(progress.load(Ordering::SeqCst), 1);
    let other = ActionContext::default().with_shared(Arc::new(AtomicUsize::new(1)));
    assert_ne!(context, other);
}

#[test]
fn absent_and_mismatched_shared_objects_are_distinct_explicit_errors() {
    assert_eq!(
        ActionContext::default().shared::<usize>().unwrap_err(),
        SharedStateError::NotProvided
    );
    let context = ActionContext::default().with_shared(Arc::new(7_usize));
    assert!(matches!(
        context.shared::<String>(),
        Err(SharedStateError::TypeMismatch {
            expected: "alloc::string::String",
            actual: "usize"
        })
    ));
}

#[test]
fn debug_and_equality_do_not_read_or_format_application_payloads() {
    struct Secret;
    impl std::fmt::Debug for Secret {
        fn fmt(&self, _: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            panic!("must not inspect shared contents")
        }
    }
    let state = Arc::new(Secret);
    let context = ActionContext::default().with_shared(state);
    assert_eq!(context, context.clone());
    assert!(format!("{context:?}").contains("SharedState"));
}
