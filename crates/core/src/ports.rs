use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fmt::{self, Display, Formatter};

/// Stable component identity during one port resolution.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct ComponentId(usize);

impl ComponentId {
    #[must_use]
    pub const fn new(index: usize) -> Self {
        Self(index)
    }

    #[must_use]
    pub const fn index(self) -> usize {
        self.0
    }
}

/// Stable port identity during one port resolution.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct PortId(usize);

impl PortId {
    #[must_use]
    pub const fn new(index: usize) -> Self {
        Self(index)
    }

    #[must_use]
    pub const fn index(self) -> usize {
        self.0
    }
}

/// Stable requirement identity used by an explicit provider selection.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct RequirementId(usize);

impl RequirementId {
    #[must_use]
    pub const fn new(index: usize) -> Self {
        Self(index)
    }

    #[must_use]
    pub const fn index(self) -> usize {
        self.0
    }
}

/// A requirement either accepts one deterministic provider or names it exactly.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PortRequirement {
    Any {
        consumer: ComponentId,
        port: PortId,
    },
    Exact {
        consumer: ComponentId,
        port: PortId,
        provider: ComponentId,
    },
}

impl PortRequirement {
    #[must_use]
    pub const fn consumer(self) -> ComponentId {
        match self {
            Self::Any { consumer, .. } | Self::Exact { consumer, .. } => consumer,
        }
    }

    #[must_use]
    pub const fn port(self) -> PortId {
        match self {
            Self::Any { port, .. } | Self::Exact { port, .. } => port,
        }
    }
}

/// An explicit provider choice for one any-provider requirement.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ProviderSelection {
    requirement: RequirementId,
    provider: ComponentId,
}

impl ProviderSelection {
    #[must_use]
    pub const fn new(requirement: RequirementId, provider: ComponentId) -> Self {
        Self {
            requirement,
            provider,
        }
    }
}

/// One fully resolved port connection.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ResolvedPortConnection {
    pub provider: ComponentId,
    pub consumer: ComponentId,
    pub port: PortId,
}

/// A deterministic port resolution failure.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PortResolutionError {
    UnknownConsumer(ComponentId),
    UnknownProvider(ComponentId),
    DuplicateSelection(RequirementId),
    UnknownSelectionRequirement(RequirementId),
    SelectionForExactRequirement(RequirementId),
    MixedRequirementKinds {
        consumer: ComponentId,
        port: PortId,
    },
    DuplicateAnyRequirement {
        consumer: ComponentId,
        port: PortId,
    },
    DuplicateExactRequirement {
        consumer: ComponentId,
        port: PortId,
        provider: ComponentId,
    },
    NoProvider {
        consumer: ComponentId,
        port: PortId,
    },
    AmbiguousProvider {
        consumer: ComponentId,
        port: PortId,
        candidates: Vec<ComponentId>,
    },
    ProviderDoesNotOffer {
        provider: ComponentId,
        port: PortId,
    },
    UnusedSelection(RequirementId),
}

impl Display for PortResolutionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownConsumer(component) => write!(
                formatter,
                "port requirement has unknown consumer {}",
                component.index()
            ),
            Self::UnknownProvider(component) => write!(
                formatter,
                "port requirement has unknown provider {}",
                component.index()
            ),
            Self::DuplicateSelection(requirement) => write!(
                formatter,
                "port requirement {} has multiple provider selections",
                requirement.index()
            ),
            Self::UnknownSelectionRequirement(requirement) => write!(
                formatter,
                "provider selection references unknown requirement {}",
                requirement.index()
            ),
            Self::SelectionForExactRequirement(requirement) => write!(
                formatter,
                "provider selection references exact requirement {}",
                requirement.index()
            ),
            Self::MixedRequirementKinds { consumer, port } => write!(
                formatter,
                "port input mixes any and exact requirements: consumer {} port {}",
                consumer.index(),
                port.index()
            ),
            Self::DuplicateAnyRequirement { consumer, port } => write!(
                formatter,
                "port requirement is declared more than once: consumer {} port {}",
                consumer.index(),
                port.index()
            ),
            Self::DuplicateExactRequirement {
                consumer,
                port,
                provider,
            } => write!(
                formatter,
                "exact port requirement is declared more than once: consumer {} port {} provider {}",
                consumer.index(),
                port.index(),
                provider.index()
            ),
            Self::NoProvider { consumer, port } => write!(
                formatter,
                "port has no provider: consumer {} port {}",
                consumer.index(),
                port.index()
            ),
            Self::AmbiguousProvider {
                consumer,
                port,
                candidates,
            } => write!(
                formatter,
                "port provider is ambiguous: consumer {} port {} candidates {:?}",
                consumer.index(),
                port.index(),
                candidates
                    .iter()
                    .map(|candidate| candidate.index())
                    .collect::<Vec<_>>()
            ),
            Self::ProviderDoesNotOffer { provider, port } => write!(
                formatter,
                "selected component {} does not provide port {}",
                provider.index(),
                port.index()
            ),
            Self::UnusedSelection(requirement) => write!(
                formatter,
                "provider selection has no matching any-provider requirement: {}",
                requirement.index()
            ),
        }
    }
}

impl Error for PortResolutionError {}

/// Resolves every requirement in declaration order.
///
/// # Errors
///
/// Returns [`PortResolutionError`] when declarations are ambiguous,
/// duplicated, inconsistent, or reference unknown components.
pub fn resolve_ports(
    component_outputs: &[Vec<PortId>],
    requirements: &[PortRequirement],
    selections: &[ProviderSelection],
) -> Result<Vec<ResolvedPortConnection>, PortResolutionError> {
    let providers = index_providers(component_outputs);
    let selection_by_requirement = index_selections(requirements, selections)?;
    validate_requirement_groups(component_outputs.len(), requirements)?;

    let mut used_selections = HashSet::new();
    let mut bindings = Vec::with_capacity(requirements.len());
    for (index, requirement) in requirements.iter().copied().enumerate() {
        let requirement_id = RequirementId::new(index);
        let consumer = requirement.consumer();
        let port = requirement.port();
        let provider = match requirement {
            PortRequirement::Exact { provider, .. } => provider,
            PortRequirement::Any { .. } => {
                let candidates = providers.get(&port).map_or(&[][..], Vec::as_slice);
                if let Some(provider) = selection_by_requirement.get(&requirement_id) {
                    used_selections.insert(requirement_id);
                    if !candidates.contains(provider) {
                        return Err(PortResolutionError::ProviderDoesNotOffer {
                            provider: *provider,
                            port,
                        });
                    }
                    *provider
                } else {
                    match candidates {
                        [] => {
                            return Err(PortResolutionError::NoProvider { consumer, port });
                        }
                        [provider] => *provider,
                        _ => {
                            return Err(PortResolutionError::AmbiguousProvider {
                                consumer,
                                port,
                                candidates: candidates.to_vec(),
                            });
                        }
                    }
                }
            }
        };
        require_component(component_outputs.len(), provider, false)?;
        if !component_outputs[provider.index()].contains(&port) {
            return Err(PortResolutionError::ProviderDoesNotOffer { provider, port });
        }
        bindings.push(ResolvedPortConnection {
            provider,
            consumer,
            port,
        });
    }

    for requirement in selection_by_requirement.keys() {
        if !used_selections.contains(requirement) {
            return Err(PortResolutionError::UnusedSelection(*requirement));
        }
    }
    Ok(bindings)
}

fn index_providers(component_outputs: &[Vec<PortId>]) -> HashMap<PortId, Vec<ComponentId>> {
    let mut providers: HashMap<PortId, Vec<ComponentId>> = HashMap::new();
    for (index, outputs) in component_outputs.iter().enumerate() {
        let component = ComponentId::new(index);
        for port in outputs {
            let candidates = providers.entry(*port).or_default();
            if !candidates.contains(&component) {
                candidates.push(component);
            }
        }
    }
    providers
}

fn index_selections(
    requirements: &[PortRequirement],
    selections: &[ProviderSelection],
) -> Result<HashMap<RequirementId, ComponentId>, PortResolutionError> {
    let mut indexed = HashMap::new();
    for selection in selections {
        let Some(requirement) = requirements.get(selection.requirement.index()) else {
            return Err(PortResolutionError::UnknownSelectionRequirement(
                selection.requirement,
            ));
        };
        if matches!(requirement, PortRequirement::Exact { .. }) {
            return Err(PortResolutionError::SelectionForExactRequirement(
                selection.requirement,
            ));
        }
        if indexed
            .insert(selection.requirement, selection.provider)
            .is_some()
        {
            return Err(PortResolutionError::DuplicateSelection(
                selection.requirement,
            ));
        }
    }
    Ok(indexed)
}

fn validate_requirement_groups(
    component_count: usize,
    requirements: &[PortRequirement],
) -> Result<(), PortResolutionError> {
    let mut kinds = HashMap::new();
    let mut any = HashSet::new();
    let mut exact = HashSet::new();
    for requirement in requirements {
        let consumer = requirement.consumer();
        let port = requirement.port();
        require_component(component_count, consumer, true)?;
        let is_any = matches!(requirement, PortRequirement::Any { .. });
        if let Some(previous) = kinds.get(&(consumer, port)) {
            if *previous != is_any {
                return Err(PortResolutionError::MixedRequirementKinds { consumer, port });
            }
        } else {
            kinds.insert((consumer, port), is_any);
        }
        match requirement {
            PortRequirement::Any { .. } => {
                if !any.insert((consumer, port)) {
                    return Err(PortResolutionError::DuplicateAnyRequirement { consumer, port });
                }
            }
            PortRequirement::Exact { provider, .. } => {
                require_component(component_count, *provider, false)?;
                if !exact.insert((consumer, port, *provider)) {
                    return Err(PortResolutionError::DuplicateExactRequirement {
                        consumer,
                        port,
                        provider: *provider,
                    });
                }
            }
        }
    }
    Ok(())
}

fn require_component(
    component_count: usize,
    component: ComponentId,
    consumer: bool,
) -> Result<(), PortResolutionError> {
    if component.index() < component_count {
        return Ok(());
    }
    if consumer {
        Err(PortResolutionError::UnknownConsumer(component))
    } else {
        Err(PortResolutionError::UnknownProvider(component))
    }
}

#[cfg(test)]
mod tests {
    use super::{
        ComponentId, PortId, PortRequirement, PortResolutionError, ProviderSelection,
        RequirementId, ResolvedPortConnection, resolve_ports,
    };

    #[test]
    fn resolves_selected_any_provider_and_exact_provider() {
        let port = PortId::new(0);
        let bindings = resolve_ports(
            &[vec![port], vec![port], vec![], vec![]],
            &[
                PortRequirement::Any {
                    consumer: ComponentId::new(2),
                    port,
                },
                PortRequirement::Exact {
                    consumer: ComponentId::new(3),
                    port,
                    provider: ComponentId::new(0),
                },
            ],
            &[ProviderSelection::new(
                RequirementId::new(0),
                ComponentId::new(1),
            )],
        )
        .unwrap();

        assert_eq!(
            bindings,
            [
                ResolvedPortConnection {
                    provider: ComponentId::new(1),
                    consumer: ComponentId::new(2),
                    port,
                },
                ResolvedPortConnection {
                    provider: ComponentId::new(0),
                    consumer: ComponentId::new(3),
                    port,
                },
            ]
        );
    }

    #[test]
    fn rejects_an_ambiguous_any_provider_requirement() {
        let port = PortId::new(0);
        let result = resolve_ports(
            &[vec![port], vec![port], vec![]],
            &[PortRequirement::Any {
                consumer: ComponentId::new(2),
                port,
            }],
            &[],
        );

        assert_eq!(
            result,
            Err(PortResolutionError::AmbiguousProvider {
                consumer: ComponentId::new(2),
                port,
                candidates: vec![ComponentId::new(0), ComponentId::new(1)],
            })
        );
    }

    #[test]
    fn rejects_multiple_selections_for_one_requirement() {
        let port = PortId::new(0);
        let result = resolve_ports(
            &[vec![port], vec![port], vec![]],
            &[PortRequirement::Any {
                consumer: ComponentId::new(2),
                port,
            }],
            &[
                ProviderSelection::new(RequirementId::new(0), ComponentId::new(0)),
                ProviderSelection::new(RequirementId::new(0), ComponentId::new(1)),
            ],
        );

        assert_eq!(
            result,
            Err(PortResolutionError::DuplicateSelection(RequirementId::new(
                0
            )))
        );
    }
}
