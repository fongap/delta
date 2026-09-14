//! The single product agent definition owned by the Rust runtime.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspacePolicy {
    /// Start with a per-task workspace and allow the user to grant more roots.
    TaskWorkspace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AgentDefinition {
    pub id: &'static str,
    pub name: &'static str,
    pub system_prompt: &'static str,
    pub capabilities: &'static [&'static str],
    pub workspace_policy: WorkspacePolicy,
}

pub const DELTA_AGENT: AgentDefinition = AgentDefinition {
    id: "delta",
    name: "Delta",
    system_prompt: "You are Delta, a careful desktop assistant for office work, research analysis, content creation, and task-focused scripts. Use only the capabilities exposed by the Rust runtime. Treat the workspace and granted roots as the complete filesystem boundary. Ask for approval when required, cite sources when using research results, and describe created artifacts clearly.",
    capabilities: &["files", "search", "shell", "todo", "mcp"],
    workspace_policy: WorkspacePolicy::TaskWorkspace,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delta_is_the_only_static_product_agent() {
        assert_eq!(DELTA_AGENT.id, "delta");
        assert_eq!(DELTA_AGENT.name, "Delta");
        assert!(DELTA_AGENT.system_prompt.contains("office work"));
        assert!(DELTA_AGENT.capabilities.contains(&"mcp"));
        assert_eq!(DELTA_AGENT.workspace_policy, WorkspacePolicy::TaskWorkspace);
    }
}
