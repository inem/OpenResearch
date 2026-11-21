//! Opt-in private native configuration, sharing only the selected account's credentials.

use std::path::{Path, PathBuf};

use crate::error::{anyhow, Context, Result};

pub(crate) fn enabled() -> bool {
    std::env::var_os("ORX_PRIVATE_AGENT_SETTINGS").is_some_and(|value| value == "1")
}

pub(crate) fn config_store() -> super::native_store::NativeStore {
    if enabled() {
        super::native_store::NativeStore::Isolated
    } else {
        super::native_store::NativeStore::Legacy
    }
}

pub(crate) fn check_data_dir(data: &Path, bind: bool) -> Result<()> {
    if let Some(value) = std::env::var_os("ORX_PRIVATE_AGENT_SETTINGS") {
        if value != "1" {
            return Err(anyhow!("ORX_PRIVATE_AGENT_SETTINGS must be 1 or unset"));
        }
    }
    if enabled() && std::env::var_os("ORX_DATA_DIR").is_none() {
        return Err(anyhow!(
            "Private agent settings require an explicit ORX_DATA_DIR"
        ));
    }
    if enabled() && !data.is_absolute() {
        return Err(anyhow!(
            "Private agent settings require an absolute ORX_DATA_DIR"
        ));
    }
    check_binding(data, enabled(), bind)
}

fn check_binding(data: &Path, private: bool, bind: bool) -> Result<()> {
    let marker = data.join("agent-settings-policy.json");
    match std::fs::read_to_string(&marker) {
        Ok(policy) if policy == "\"auth-only-v1\"\n" && private => Ok(()),
        Ok(_) => Err(anyhow!("This data directory uses a different agent settings policy. Restore ORX_PRIVATE_AGENT_SETTINGS=1 or choose a fresh ORX_DATA_DIR.")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            if private {
                if data.join("orx.db").try_exists()? || data.join("agents").try_exists()? {
                    return Err(anyhow!("Private agent settings need a fresh ORX_DATA_DIR; existing chats or agent homes cannot be adopted."));
                }
                if bind {
                    super::git::atomic_write(&marker, b"\"auth-only-v1\"\n")?;
                }
            }
            Ok(())
        }
        Err(e) => Err(e).with_context(|| format!("Could not read {}", marker.display())),
    }
}

pub(crate) fn reject_legacy(store: super::native_store::NativeStore) -> Result<()> {
    if super::domain_profile::configured() && !enabled() {
        return Err(anyhow!("Custom Claude/Codex profiles require ORX_PRIVATE_AGENT_SETTINGS=1 and a fresh instance directory; their skill policy must not rewrite shared agent settings."));
    }
    if enabled() && store == super::native_store::NativeStore::Legacy {
        return Err(anyhow!("This instance cannot resume a chat in the shared agent home. Start a new chat in this instance."));
    }
    Ok(())
}

/// Never adopt private files back into the account owner, or fall back to a credential copy.
pub(crate) fn prepare_home(root: &Path, auth: Option<&Path>) -> Result<()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    std::fs::create_dir_all(root)?;
    if std::fs::symlink_metadata(root)?.file_type().is_symlink() {
        return Err(anyhow!(
            "Private agent home cannot be a symlink: {}",
            root.display()
        ));
    }
    let auth_name = auth.and_then(Path::file_name);
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let name = entry.file_name();
        if name.to_string_lossy().ends_with(".orx-managed-link")
            || (entry.file_type()?.is_symlink() && Some(name.as_os_str()) != auth_name)
        {
            return Err(anyhow!("Private agent home contains an inherited link: {}. Use a fresh instance directory.", entry.path().display()));
        }
    }
    let Some(source) = auth else { return Ok(()) };
    let source: PathBuf = std::path::absolute(source)?;
    let destination = root.join(
        source
            .file_name()
            .ok_or_else(|| anyhow!("Credential path has no filename"))?,
    );
    match std::fs::symlink_metadata(&destination) {
        Ok(meta) if meta.file_type().is_symlink() && std::fs::read_link(&destination)? == source => return Ok(()),
        Ok(_) => return Err(anyhow!("Credential bridge was replaced at {}. Refusing to overwrite either account; use a fresh instance directory.", destination.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
        Err(e) => return Err(e.into()),
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&source, &destination)?;
    #[cfg(windows)]
    std::os::windows::fs::symlink_file(&source, &destination).context(
        "Shared credentials require file symlink support; copies would lose refreshed credentials",
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn native_preparation_and_env_use_private_state() {
        if !crate::config::in_test_child(&[]) {
            return;
        }
        std::env::set_var("ORX_PRIVATE_AGENT_SETTINGS", "1");
        use super::super::native_store::{self, NativeStore};
        let owner = native_store::codex_home(NativeStore::Legacy);
        std::fs::create_dir_all(owner.join("skills")).unwrap();
        std::fs::write(owner.join("auth.json"), "synthetic auth").unwrap();
        std::fs::write(owner.join("config.toml"), "owner settings").unwrap();
        std::fs::write(owner.join("skills/SKILL.md"), "owner skill").unwrap();
        let codex = native_store::prepare_codex(NativeStore::Isolated).unwrap();
        assert_eq!(
            std::fs::read_to_string(codex.join("auth.json")).unwrap(),
            "synthetic auth"
        );
        assert!(!codex.join("config.toml").exists());
        assert!(!codex.join("skills").exists());
        std::fs::write(codex.join("config.toml"), "instance settings").unwrap();
        native_store::prepare_codex(NativeStore::Isolated).unwrap();
        assert_eq!(
            std::fs::read_to_string(owner.join("config.toml")).unwrap(),
            "owner settings"
        );
        assert!(native_store::prepare_codex(NativeStore::Legacy).is_err());

        let claude_owner = native_store::claude_home(NativeStore::Legacy);
        std::fs::create_dir_all(&claude_owner).unwrap();
        std::fs::write(
            claude_owner.join(".credentials.json"),
            "synthetic claude auth",
        )
        .unwrap();
        std::fs::write(claude_owner.join("settings.json"), "owner settings").unwrap();
        std::fs::write(
            native_store::claude_config_file(),
            r#"{"mcpServers":{"outside":{}},"projects":{"outside":{}}}"#,
        )
        .unwrap();
        let claude = native_store::prepare_claude(NativeStore::Isolated).unwrap();
        assert_eq!(
            std::fs::read_to_string(claude.join(".credentials.json")).unwrap(),
            "synthetic claude auth"
        );
        assert!(!claude.join("settings.json").exists());
        let account: serde_json::Value =
            serde_json::from_slice(&std::fs::read(claude.join(".claude.json")).unwrap()).unwrap();
        assert!(account.get("mcpServers").is_none());
        assert!(account.get("projects").is_none());
        assert!(native_store::prepare_claude(NativeStore::Legacy).is_err());

        std::fs::create_dir_all(owner.join("sessions")).unwrap();
        std::fs::write(owner.join("sessions/outside-session.jsonl"), "{}").unwrap();
        assert!(native_store::codex_session("outside-session")
            .unwrap()
            .is_none());
        std::fs::create_dir_all(codex.join("sessions")).unwrap();
        std::fs::write(codex.join("sessions/inside-session.jsonl"), "{}").unwrap();
        assert_eq!(
            native_store::codex_session("inside-session")
                .unwrap()
                .unwrap()
                .store,
            NativeStore::Isolated
        );
        assert!(!super::super::native_chats::is_importable("codex"));
        assert_eq!(
            super::super::harness::registry()
                .iter()
                .map(|h| h.id())
                .collect::<Vec<_>>(),
            vec!["claude-code", "codex"]
        );

        std::env::set_var("ANTHROPIC_BASE_URL", "https://outside.invalid");
        std::env::set_var("OPENAI_API_KEY", "synthetic-outside-key");
        let mut command = crate::process::tokio_command("does-not-run");
        super::super::chat::prepare_env(&mut command);
        let environment: std::collections::HashMap<_, _> = command.as_std().get_envs().collect();
        assert_eq!(
            environment.get(std::ffi::OsStr::new("ANTHROPIC_BASE_URL")),
            Some(&None)
        );
        assert_eq!(
            environment.get(std::ffi::OsStr::new("OPENAI_API_KEY")),
            Some(&None)
        );

        let global_env = crate::config::test_home().join(".openresearch/env");
        std::fs::create_dir_all(global_env.parent().unwrap()).unwrap();
        std::fs::write(&global_env, "export PRIVATE_PROBE='global'\n").unwrap();
        assert!(crate::config::synced_env_var("PRIVATE_PROBE").is_none());
        crate::config::write_synced_env_var("PRIVATE_PROBE", "instance").unwrap();
        assert_eq!(
            crate::config::synced_env_var("PRIVATE_PROBE").as_deref(),
            Some("instance")
        );
        assert_eq!(
            crate::config::list_synced_env(),
            vec![("PRIVATE_PROBE".into(), "instance".into())]
        );
        crate::config::remove_synced_env_var("PRIVATE_PROBE").unwrap();
        assert!(crate::config::list_synced_env().is_empty());
        assert_eq!(
            std::fs::read_to_string(global_env).unwrap(),
            "export PRIVATE_PROBE='global'\n"
        );
    }

    #[test]
    fn private_settings_policy_is_persistent_and_requires_fresh_state() {
        let temp = crate::local::git::TemporaryDirectory::new("orx-private-settings").unwrap();
        check_binding(temp.path(), true, true).unwrap();
        std::fs::write(temp.path().join("orx.db"), "state").unwrap();
        check_binding(temp.path(), true, true).unwrap();
        assert!(check_binding(temp.path(), false, false).is_err());
        let old = crate::local::git::TemporaryDirectory::new("orx-private-settings").unwrap();
        std::fs::create_dir(old.path().join("agents")).unwrap();
        assert!(check_binding(old.path(), true, true).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn credential_bridge_shares_rotation_without_sharing_settings() {
        let temp = crate::local::git::TemporaryDirectory::new("orx-private-settings").unwrap();
        let owner = temp.path().join("owner");
        let a = temp.path().join("a");
        let b = temp.path().join("b");
        std::fs::create_dir(&owner).unwrap();
        let auth = owner.join("auth.json");
        std::fs::write(&auth, "synthetic-credential-1").unwrap();
        std::fs::write(owner.join("config.toml"), "owner settings").unwrap();
        for home in [&a, &b] {
            prepare_home(home, Some(&auth)).unwrap();
        }
        std::fs::write(a.join("config.toml"), "a settings").unwrap();
        std::fs::write(b.join("config.toml"), "b settings").unwrap();
        std::fs::write(owner.join("new-auth"), "synthetic-credential-2").unwrap();
        std::fs::rename(owner.join("new-auth"), &auth).unwrap();
        for home in [&a, &b] {
            prepare_home(home, Some(&auth)).unwrap();
            assert_eq!(
                std::fs::read_to_string(home.join("auth.json")).unwrap(),
                "synthetic-credential-2"
            );
            for name in ["skills", "plugins", "memories", "AGENTS.md", "CLAUDE.md"] {
                assert!(!home.join(name).exists());
            }
        }
        assert_eq!(
            std::fs::read_to_string(a.join("config.toml")).unwrap(),
            "a settings"
        );
        assert_eq!(
            std::fs::read_to_string(b.join("config.toml")).unwrap(),
            "b settings"
        );
        assert_eq!(
            std::fs::read_to_string(owner.join("config.toml")).unwrap(),
            "owner settings"
        );
        std::os::unix::fs::symlink(owner.join("config.toml"), a.join("settings.json")).unwrap();
        assert!(prepare_home(&a, Some(&auth)).is_err());
        std::fs::remove_file(b.join("auth.json")).unwrap();
        std::fs::write(b.join("auth.json"), "diverged credential").unwrap();
        assert!(prepare_home(&b, Some(&auth)).is_err());
    }
}
