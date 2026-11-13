use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::path::PathBuf;
use std::process::{Command, Stdio};

use crate::{Action, ActionContext, ActionError};

/// A native action that executes one program without a command shell.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessAction {
    program: String,
    arguments: Vec<String>,
    cwd: PathBuf,
    environment: BTreeMap<String, String>,
}

impl ProcessAction {
    #[must_use]
    pub fn new(
        program: impl Into<String>,
        arguments: impl IntoIterator<Item = impl Into<String>>,
        cwd: PathBuf,
        environment: BTreeMap<String, String>,
    ) -> Self {
        Self {
            program: program.into(),
            arguments: arguments.into_iter().map(Into::into).collect(),
            cwd,
            environment,
        }
    }
}

impl Action for ProcessAction {
    type Inputs = ();
    type Output = ();
    fn execute(&self, context: &ActionContext, (): ()) -> Result<(), ActionError> {
        let mut command = Command::new(&self.program);
        command.args(&self.arguments);
        configure_command(&mut command, &self.cwd, &self.environment, context)?;
        run_command(command, &self.program)
    }
}

fn configure_command(
    command: &mut Command,
    cwd: &PathBuf,
    environment: &BTreeMap<String, String>,
    context: &ActionContext,
) -> Result<(), ActionError> {
    command.current_dir(cwd);
    command.env_clear().envs(environment);
    if let Some(path) = context.component_log_path() {
        let stdout = append_log_file(path)?;
        let stderr = stdout.try_clone().map_err(|error| {
            ActionError::new(format!(
                "failed to clone native action log file {}: {error}",
                path.display()
            ))
        })?;
        command.stdout(Stdio::from(stdout));
        command.stderr(Stdio::from(stderr));
    }
    Ok(())
}

fn append_log_file(path: &std::path::Path) -> Result<File, ActionError> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| {
            ActionError::new(format!(
                "failed to open native action log file {}: {error}",
                path.display()
            ))
        })
}

fn run_command(mut command: Command, display_name: &str) -> Result<(), ActionError> {
    let status = command.status().map_err(|error| {
        ActionError::new(format!(
            "failed to execute native action {display_name}: {error}"
        ))
    })?;
    if status.success() {
        return Ok(());
    }
    Err(ActionError::new(format!(
        "native action {display_name} exited with status {status}"
    )))
}
