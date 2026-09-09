//! Policy Authority (R2, ADR-030).
//!
//! This module provides the sole trusted authority for tool call policy evaluation.
//! Policy evaluation is a pure deterministic function that classifies tool calls
//! into risk levels (L0-L4) and applies the four policy slices:
//!
//! 1. **Slice 1 (classify)**: Determine the base risk level from metadata
//! 2. **Slice 2 (enforce_level)**: L4 (irreversible/sensitive) is never auto-allowed
//! 3. **Slice 3 (enforce_scope)**: Resource confinement for side-effectful calls
//! 4. **Slice 4a (restrict_grants)**: L3+ external effects require explicit approval or policy
//!
//! Contract: `docs/architecture/adr/ADR-030-r2-policy-hard-cut.md`
//! and `core/gateway.py`, `core/permissions.py`, `core/risk.py` (Python facades mirror these).

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ShadowReadError;

/// Current policy schema version.
pub const POLICY_SCHEMA_VERSION: i64 = 1;

/// Risk level for a tool call (L0-L4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RiskLevel {
    L0 = 0, // read-only, no side effects
    L1 = 1, // reversible local writes (checkpointed)
    L2 = 2, // consequential local writes / config changes
    L3 = 3, // external effects, compensatable
    L4 = 4, // irreversible or sensitive — never auto-allowed
}

impl RiskLevel {
    pub fn from_i64(v: i64) -> Option<Self> {
        match v {
            0 => Some(RiskLevel::L0),
            1 => Some(RiskLevel::L1),
            2 => Some(RiskLevel::L2),
            3 => Some(RiskLevel::L3),
            4 => Some(RiskLevel::L4),
            _ => None,
        }
    }
}

/// Risk class (intrinsic side-effect category).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RiskClass {
    Read = 0,
    WriteLocal = 1,
    Exec = 2,
    External = 3,
    Egress = 4,
}

/// Tool metadata for classification.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ToolMetadata {
    pub risk_level: Option<String>,
    pub requires_approval: Option<bool>,
    pub category: Option<String>,
    pub capabilities: Option<Vec<String>>,
}

/// A permission decision from the PermissionEngine.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Decision {
    pub allowed: bool,
    pub reason: String,
    pub needs_user: bool,
    pub rule: String,
    pub grant: String,
}

/// Input for policy evaluation.
#[derive(Debug, Clone, Deserialize)]
pub struct PolicyEvaluateInput {
    pub tool_name: String,
    pub arguments: Option<Value>,
    pub metadata: Option<ToolMetadata>,
    pub decision: Decision,
    pub level: i64,
    pub workspace_root: String,
    pub roots: Vec<RootEntry>,
}

/// A root directory entry for confinement checking.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RootEntry {
    pub path: String,
    pub writable: bool,
}

/// Output of policy evaluation.
#[derive(Debug, Clone, Serialize)]
pub struct PolicyEvaluateOutput {
    pub decision: Decision,
    pub level: i64,
}

/// Tools whose effect is irreversible regardless of metadata.
const IRREVERSIBLE_TOOLS: &[&str] = &["send_email"];

/// Tools whose effect is network egress (model-chosen outbound request).
const EGRESS_TOOLS: &[&str] = &[
    "web_fetch",
    "web_search",
    "browser_read_url",
    "browser_open_url",
];

/// Valid metadata risk levels.
const VALID_METADATA_RISK: &[&str] = &["low", "medium", "high"];

/// Metadata categories whose medium-risk, approval-gated tools are LOCAL effects.
const LOCAL_CATEGORIES: &[&str] = &["filesystem"];

/// Metadata categories whose low-risk, non-approval tools are REVERSIBLE local writes.
const REVERSIBLE_WRITE_CATEGORIES: &[(&str, &[&str])] = &[("memory", &["remember"])];

/// Resource argument names.
const RESOURCE_ARGS: &[&str] = &[
    "path",
    "file_path",
    "filepath",
    "file",
    "filename",
    "attachment",
    "attachments",
    "document",
    "resource",
    "title",
];

/// Path argument names.
const PATH_ARGS: &[&str] = &["path", "file_path", "filepath", "file"];

/// Patch blob argument names per tool.
const PATCH_BLOB_ARGS: &[(&str, &str)] =
    &[("apply_patch", "patch"), ("apply_unified_diff", "diff")];

/// Sensitivity tokens (substring match on resource path).
const SENSITIVE_TOKENS: &[&str] = &[
    // payroll / HR
    "工资",
    "薪资",
    "薪酬",
    "绩效",
    "payroll",
    "salary",
    "compensation",
    // identity / government records
    "身份证",
    "护照",
    "社保",
    "passport",
    "national_id",
    "ssn",
    // financial accounts
    "银行卡",
    "银行流水",
    "bank_statement",
    // credentials / keys / secrets
    "id_rsa",
    ".pem",
    ".pfx",
    ".p12",
    ".keystore",
    ".env",
    "credential",
    "password",
    "secret",
];

/// Patch regex patterns.
const APPLY_PATCH_FILE: &str = r"^\*\*\* (?:Add|Update|Delete) File: (.+)$";
const APPLY_PATCH_MOVE: &str = r"^\*\*\* Move to: (.+)$";
const UNIFIED_DIFF_FILE: &str = r"^\+\+\+ (?:b/)?(.+?)\s*$";

/// Compiled regex patterns (lazy-initialized to avoid recompilation).
static RE_APPLY_PATCH_FILE: OnceLock<regex::Regex> = OnceLock::new();
static RE_APPLY_PATCH_MOVE: OnceLock<regex::Regex> = OnceLock::new();
static RE_UNIFIED_DIFF_FILE: OnceLock<regex::Regex> = OnceLock::new();

fn get_re_file() -> &'static regex::Regex {
    RE_APPLY_PATCH_FILE.get_or_init(|| regex::Regex::new(APPLY_PATCH_FILE).unwrap())
}

fn get_re_move() -> &'static regex::Regex {
    RE_APPLY_PATCH_MOVE.get_or_init(|| regex::Regex::new(APPLY_PATCH_MOVE).unwrap())
}

fn get_re_diff() -> &'static regex::Regex {
    RE_UNIFIED_DIFF_FILE.get_or_init(|| regex::Regex::new(UNIFIED_DIFF_FILE).unwrap())
}

/// Classify a tool call into a risk level (L0-L4).
pub fn classify(
    tool_name: &str,
    arguments: Option<&Value>,
    metadata: Option<&ToolMetadata>,
) -> RiskLevel {
    // Reversibility first: explicitly irreversible tool is L4.
    if IRREVERSIBLE_TOOLS.contains(&tool_name) {
        return RiskLevel::L4;
    }

    // Egress floor: model-chosen outbound request is L3 by name.
    if EGRESS_TOOLS.contains(&tool_name) {
        return RiskLevel::L3;
    }

    // Fail closed: no metadata or unknown risk value is L4.
    let metadata = match metadata {
        Some(m) => m,
        None => return RiskLevel::L4,
    };

    let risk = metadata.risk_level.as_deref().unwrap_or("").to_lowercase();
    let requires_approval = metadata.requires_approval.unwrap_or(false);
    let category = metadata.category.as_deref().unwrap_or("").to_lowercase();

    if !VALID_METADATA_RISK.contains(&risk.as_str()) {
        return RiskLevel::L4;
    }

    let base = band_level(&risk, requires_approval, &category, metadata);

    // Sensitivity: L3 touching sensitive resource escalates to L4.
    if base == RiskLevel::L3 && touches_sensitive_resource(arguments) {
        return RiskLevel::L4;
    }

    base
}

/// Compute base risk level from metadata (no argument inspection).
fn band_level(
    risk: &str,
    requires_approval: bool,
    category: &str,
    metadata: &ToolMetadata,
) -> RiskLevel {
    match risk {
        "high" => {
            // Arbitrary local execution (shell): consequential and unsandboxed.
            RiskLevel::L3
        }
        "medium" => {
            if requires_approval {
                // Approval-gated medium risk is L3 ONLY for external effects;
                // local checkpointed writes stay at L2.
                if LOCAL_CATEGORIES.contains(&category) {
                    RiskLevel::L2
                } else {
                    RiskLevel::L3
                }
            } else {
                RiskLevel::L2
            }
        }
        "low" => {
            if requires_approval {
                RiskLevel::L2
            } else {
                let write_caps = REVERSIBLE_WRITE_CATEGORIES
                    .iter()
                    .find(|(cat, _)| *cat == category)
                    .map(|(_, caps)| *caps)
                    .unwrap_or(&[]);
                let caps = metadata.capabilities.as_deref().unwrap_or(&[]);
                if write_caps.iter().any(|cap| caps.contains(&cap.to_string())) {
                    RiskLevel::L1
                } else {
                    RiskLevel::L0
                }
            }
        }
        _ => RiskLevel::L4,
    }
}

/// Check if any declared resource matches a sensitivity token.
fn touches_sensitive_resource(arguments: Option<&Value>) -> bool {
    for resource in declared_resources(arguments) {
        let haystack = resource.to_lowercase();
        if SENSITIVE_TOKENS
            .iter()
            .any(|token| haystack.contains(token))
        {
            return true;
        }
    }
    false
}

/// Extract declared resources from arguments.
fn declared_resources(arguments: Option<&Value>) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(args) = arguments {
        for name in RESOURCE_ARGS {
            if let Some(value) = args.get(name) {
                if let Some(s) = value.as_str() {
                    let s = s.trim();
                    if !s.is_empty() && !out.contains(&s.to_string()) {
                        out.push(s.to_string());
                    }
                }
            }
        }
    }
    out
}

/// Enforce Slice 2: L4 is never auto-allowed.
pub fn enforce_level(level: RiskLevel, mut decision: Decision) -> Decision {
    if level >= RiskLevel::L4 && decision.allowed {
        decision.allowed = false;
        decision.needs_user = true;
        decision.rule = String::new();
        decision.reason = format!(
            "irreversible action (L4) — explicit approval required{}",
            if !decision.reason.is_empty() {
                format!("; was: {}", decision.reason)
            } else {
                String::new()
            }
        );
    }
    decision
}

/// Enforce Slice 4a: L3+ external effects require explicit approval or policy.
pub fn restrict_grants(level: RiskLevel, mut decision: Decision) -> Decision {
    if level < RiskLevel::L3 || !decision.allowed {
        return decision;
    }
    if decision.grant == "policy" {
        return decision;
    }
    decision.allowed = false;
    decision.needs_user = true;
    decision.rule = String::new();
    decision.reason = format!(
        "external effect (L3) requires explicit approval or standing policy{}{}",
        if !decision.grant.is_empty() {
            format!("; was auto-allowed by {} grant", decision.grant)
        } else {
            String::new()
        },
        if !decision.reason.is_empty() {
            format!("; was: {}", decision.reason)
        } else {
            String::new()
        }
    );
    decision
}

/// Extract declared on-disk targets from arguments (including patch/diff blobs).
fn declared_targets(arguments: Option<&Value>, tool_name: Option<&str>) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(args) = arguments {
        for name in PATH_ARGS {
            if let Some(value) = args.get(name) {
                if let Some(s) = value.as_str() {
                    let s = s.trim();
                    if !s.is_empty() && !out.contains(&s.to_string()) {
                        out.push(s.to_string());
                    }
                }
            }
        }
        let blob_args: Vec<&str> = if let Some(tn) = tool_name {
            PATCH_BLOB_ARGS
                .iter()
                .find(|(k, _)| *k == tn)
                .map(|(_, v)| vec![*v])
                .unwrap_or_default()
        } else {
            PATCH_BLOB_ARGS.iter().map(|(_, v)| *v).collect()
        };
        for blob_arg in blob_args {
            if let Some(blob) = args.get(blob_arg).and_then(|v| v.as_str()) {
                if blob.is_empty() {
                    continue;
                }
                // Apply patch regex patterns (using pre-compiled static regexes)
                let re_file = get_re_file();
                let re_move = get_re_move();
                let re_diff = get_re_diff();

                for cap in re_file.captures_iter(blob) {
                    if let Some(m) = cap.get(1) {
                        let p = m.as_str().trim();
                        if !p.is_empty() && !out.contains(&p.to_string()) {
                            out.push(p.to_string());
                        }
                    }
                }
                for cap in re_move.captures_iter(blob) {
                    if let Some(m) = cap.get(1) {
                        let p = m.as_str().trim();
                        if !p.is_empty() && !out.contains(&p.to_string()) {
                            out.push(p.to_string());
                        }
                    }
                }
                for cap in re_diff.captures_iter(blob) {
                    if let Some(m) = cap.get(1) {
                        let p = m.as_str().trim();
                        if p != "/dev/null" && !p.is_empty() && !out.contains(&p.to_string()) {
                            out.push(p.to_string());
                        }
                    }
                }
            }
        }
    }
    out
}

/// Enforce Slice 3: resource confinement for side-effectful calls.
pub fn enforce_scope(
    mut decision: Decision,
    arguments: Option<&Value>,
    level: RiskLevel,
    workspace_root: &str,
    roots: &[RootEntry],
    tool_name: Option<&str>,
) -> Decision {
    if !decision.allowed || level < RiskLevel::L1 {
        return decision;
    }

    let targets = declared_targets(arguments, tool_name);
    if targets.is_empty() {
        return decision;
    }

    let workspace_root = PathBuf::from(workspace_root);

    // Pre-canonicalize all roots once.
    let canon_roots: Vec<(PathBuf, bool)> = roots
        .iter()
        .map(|root| {
            let root_path = PathBuf::from(&root.path);
            let canon_root = root_path.canonicalize().unwrap_or(root_path);
            (canon_root, root.writable)
        })
        .collect();

    for target in targets {
        let p = Path::new(&target);
        let candidate = if p.is_absolute() {
            p.to_path_buf()
        } else {
            workspace_root.join(p)
        };

        // For confinement, check the parent directory (which should exist for a write).
        // If the target itself doesn't exist, canonicalize its parent instead.
        let parent = candidate.parent().unwrap_or(&candidate);
        let canon_parent = parent
            .canonicalize()
            .unwrap_or_else(|_| parent.to_path_buf());

        let mut under_any = false;
        let mut under_writable = false;
        for (canon_root, writable) in &canon_roots {
            if canon_parent.starts_with(canon_root) {
                under_any = true;
                if *writable {
                    under_writable = true;
                }
            }
        }

        if under_writable {
            continue;
        }

        let detail = if under_any {
            format!("target is in a read-only directory: {}", target)
        } else {
            format!("target is outside the trusted directories: {}", target)
        };

        decision.allowed = false;
        decision.needs_user = true;
        decision.rule = String::new();
        decision.reason = format!(
            "{} — explicit approval required{}",
            detail,
            if !decision.reason.is_empty() {
                format!("; was: {}", decision.reason)
            } else {
                String::new()
            }
        );
        return decision;
    }
    decision
}

/// Main policy evaluation entry point.
pub fn evaluate(input: PolicyEvaluateInput) -> Result<PolicyEvaluateOutput, ShadowReadError> {
    let mut decision = input.decision;

    // Slice 1: classify (already done by caller, but we re-classify for integrity)
    let level = classify(
        &input.tool_name,
        input.arguments.as_ref(),
        input.metadata.as_ref(),
    );

    // Slice 2: L4 never auto-allowed
    decision = enforce_level(level, decision);

    // Slice 4a: L3+ requires explicit approval or policy
    decision = restrict_grants(level, decision);

    // Slice 3: resource confinement
    decision = enforce_scope(
        decision,
        input.arguments.as_ref(),
        level,
        &input.workspace_root,
        &input.roots,
        Some(&input.tool_name),
    );

    Ok(PolicyEvaluateOutput {
        decision,
        level: level as i64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_metadata() -> ToolMetadata {
        ToolMetadata {
            risk_level: Some("medium".to_string()),
            requires_approval: Some(false),
            category: Some("filesystem".to_string()),
            capabilities: Some(vec![]),
        }
    }

    fn sample_decision() -> Decision {
        Decision {
            allowed: true,
            reason: "test".to_string(),
            needs_user: false,
            rule: "test_rule".to_string(),
            grant: "blanket".to_string(),
        }
    }

    #[test]
    fn classify_irreversible_tool_is_l4() {
        let level = classify("send_email", None, Some(&sample_metadata()));
        assert_eq!(level, RiskLevel::L4);
    }

    #[test]
    fn classify_egress_tool_is_l3() {
        let level = classify("web_fetch", None, Some(&sample_metadata()));
        assert_eq!(level, RiskLevel::L3);
    }

    #[test]
    fn classify_no_metadata_is_l4() {
        let level = classify("unknown_tool", None, None);
        assert_eq!(level, RiskLevel::L4);
    }

    #[test]
    fn classify_high_risk_is_l3() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("high".to_string());
        let level = classify("run_shell", None, Some(&meta));
        assert_eq!(level, RiskLevel::L3);
    }

    #[test]
    fn classify_medium_approval_local_is_l2() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("medium".to_string());
        meta.requires_approval = Some(true);
        meta.category = Some("filesystem".to_string());
        let level = classify("write_file", None, Some(&meta));
        assert_eq!(level, RiskLevel::L2);
    }

    #[test]
    fn classify_medium_approval_external_is_l3() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("medium".to_string());
        meta.requires_approval = Some(true);
        meta.category = Some("connector".to_string());
        let level = classify("send_message", None, Some(&meta));
        assert_eq!(level, RiskLevel::L3);
    }

    #[test]
    fn classify_low_reversible_write_is_l1() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("low".to_string());
        meta.category = Some("memory".to_string());
        meta.capabilities = Some(vec!["remember".to_string()]);
        let level = classify("remember", None, Some(&meta));
        assert_eq!(level, RiskLevel::L1);
    }

    #[test]
    fn classify_low_read_only_is_l0() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("low".to_string());
        meta.category = Some("read".to_string());
        let level = classify("read_file", None, Some(&meta));
        assert_eq!(level, RiskLevel::L0);
    }

    #[test]
    fn sensitivity_escalates_l3_to_l4() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("medium".to_string());
        meta.requires_approval = Some(true);
        meta.category = Some("connector".to_string());
        let args = json!({"path": "/tmp/payroll.xlsx"});
        let level = classify("send_message", Some(&args), Some(&meta));
        assert_eq!(level, RiskLevel::L4);
    }

    #[test]
    fn enforce_level_downgrades_l4() {
        let mut decision = sample_decision();
        decision = enforce_level(RiskLevel::L4, decision);
        assert!(!decision.allowed);
        assert!(decision.needs_user);
        assert!(decision.reason.contains("irreversible action (L4)"));
    }

    #[test]
    fn enforce_level_passes_l3() {
        let mut decision = sample_decision();
        decision = enforce_level(RiskLevel::L3, decision);
        assert!(decision.allowed);
    }

    #[test]
    fn restrict_grants_blocks_l3_blanket() {
        let mut decision = sample_decision();
        decision = restrict_grants(RiskLevel::L3, decision);
        assert!(!decision.allowed);
        assert!(decision.needs_user);
    }

    #[test]
    fn restrict_grants_allows_policy_grant() {
        let mut decision = sample_decision();
        decision.grant = "policy".to_string();
        decision = restrict_grants(RiskLevel::L3, decision);
        assert!(decision.allowed);
    }

    #[test]
    fn enforce_scope_allows_under_writable_root() {
        let mut decision = sample_decision();
        let args = json!({"path": "src/main.rs"});
        let roots = vec![RootEntry {
            path: "/workspace".to_string(),
            writable: true,
        }];
        decision = enforce_scope(
            decision,
            Some(&args),
            RiskLevel::L2,
            "/workspace",
            &roots,
            Some("write_file"),
        );
        assert!(decision.allowed);
    }

    #[test]
    fn enforce_scope_blocks_outside_roots() {
        let mut decision = sample_decision();
        let args = json!({"path": "/etc/passwd"});
        let roots = vec![RootEntry {
            path: "/workspace".to_string(),
            writable: true,
        }];
        decision = enforce_scope(
            decision,
            Some(&args),
            RiskLevel::L2,
            "/workspace",
            &roots,
            Some("write_file"),
        );
        assert!(!decision.allowed);
        assert!(decision.needs_user);
    }

    #[test]
    fn enforce_scope_blocks_readonly_root() {
        let mut decision = sample_decision();
        let args = json!({"path": "readonly/file.txt"});
        let roots = vec![RootEntry {
            path: "/workspace".to_string(),
            writable: false,
        }];
        decision = enforce_scope(
            decision,
            Some(&args),
            RiskLevel::L2,
            "/workspace",
            &roots,
            Some("write_file"),
        );
        assert!(!decision.allowed);
        assert!(decision.needs_user);
    }

    #[test]
    fn evaluate_full_pipeline() {
        let input = PolicyEvaluateInput {
            tool_name: "write_file".to_string(),
            arguments: Some(json!({"path": "src/main.rs"})),
            metadata: Some(sample_metadata()),
            decision: sample_decision(),
            level: 2,
            workspace_root: "/workspace".to_string(),
            roots: vec![RootEntry {
                path: "/workspace".to_string(),
                writable: true,
            }],
        };
        let output = evaluate(input).unwrap();
        assert!(output.decision.allowed);
        assert_eq!(output.level, 2);
    }

    #[test]
    fn evaluate_l4_irreversible() {
        let mut meta = sample_metadata();
        meta.risk_level = Some("high".to_string());
        let input = PolicyEvaluateInput {
            tool_name: "send_email".to_string(),
            arguments: None,
            metadata: Some(meta),
            decision: sample_decision(),
            level: 4,
            workspace_root: "/workspace".to_string(),
            roots: vec![RootEntry {
                path: "/workspace".to_string(),
                writable: true,
            }],
        };
        let output = evaluate(input).unwrap();
        assert!(!output.decision.allowed);
        assert!(output.decision.needs_user);
        assert_eq!(output.level, 4);
    }

    #[test]
    fn schema_version_constant() {
        assert_eq!(POLICY_SCHEMA_VERSION, 1);
    }
}
