# Experimental domain profiles

A profile replaces the research playbook and selects which bundled skills
are installed in chat worktrees. It also supplies files that agents can read
or execute. The example works with Source → Claim → Draft → Review; its YAML
is read by the agent, not interpreted as a database schema by OpenResearch.

From the repository root, after building:

```sh
ORX_AGENT_PROFILE="$PWD/examples/domain-profiles/content/profile.toml" \
ORX_DATA_DIR="$PWD/.domain-content-data" \
ORX_CACHE_DIR="$PWD/.domain-content-cache" \
XDG_CONFIG_HOME="$PWD/.domain-content-config" \
target/debug/orx up --no-browser --no-telemetry
```

Use directories outside a repository you intend to publish, or add these
local directories to its excludes. The usual agent sign-ins are reused by
the existing harnesses. A fresh config has its own onboarding.

For the ordinary research mode, omit `ORX_AGENT_PROFILE` and use your ordinary
data directory. Its playbook, full local skill set and LaTeX templates stay
unchanged.

The profile is a TOML manifest, using the parser already in the application:

- `version = 1` and a nonempty `name` are required.
- `playbook` names a UTF-8 Markdown file relative to the manifest.
- `bundled_skills` is an explicit list; `[]` installs no bundled skills.
  Names must exist in the local bundled catalog. The `orx-` prefix is optional.
- `resources` lists files relative to the manifest, copied under
  `.openresearch/domain/`. Paths cannot escape the profile directory.
- Unknown fields, missing files, unknown or duplicate skills and duplicate
  resources fail explicitly.

Instructions and resources are loaded once at server startup. A content hash
pins the custom profile to its data directory. To try an edited profile, use
a fresh data directory. Restarting with the same profile resumes normally;
starting with a changed profile or returning to research against those chats
fails before serving them. An existing research database cannot be retargeted.
This deliberately avoids claiming that a resident agent can forget its prior
instructions. The binding travels with data-directory moves.

The resources are installed fresh when the shared playbook preparation runs;
like upstream, exactly when this happens depends on the harness lifecycle.
Uploaded and native user skills retain their existing behavior. Unselected
bundled skills are removed from the harness's managed skill directory.

## Limits of this experiment

The CLI, research MCP tools, slash-command catalog, experiment database,
run notifications, research starter suggestions and UI still exist. The
profile selects agent guidance; it is not a tool permission boundary.
Personal and project agent instructions also still apply. It adds no
cross-session retrieval connector and no native ontology editor.

The example's checker validates references, required field presence and
literal quote membership. It does not prove claims, validate prose quality,
or prove the referenced draft file exists. Its rules are written in Python;
editing the YAML does not regenerate them.

For a second domain, copy the example directory and change its instructions,
resources and skill selection. No Rust rebuild is needed for that change.

## Validation

Checked on macOS arm64 with Rust 1.100.0:

- Debug binary built; `cargo clippy --all-targets --locked -- -D warnings` passed.
- The six domain-profile tests, fourteen existing skill tests and six existing
  agent-text tests passed. Selection and cleanup cover all four native skill
  directory layouts; user-owned skill files survive.
- The UI's localization check, generated translations/routes, TypeScript check
  and Vite build passed. No interface code changed.
- `python3 scripts/domain-profile-probe.py` drove the real server with a
  recording ACP peer. Research delivered fourteen skills; content delivered
  only `orx-agent-delegation`, the replacement playbook and domain resources.
  A second turn refreshed drift from the startup snapshot. Restart with the
  same profile resumed the chat; changed/default profiles were rejected.
- The content checker accepted a valid fixture and rejected a missing source
  reference and an invented quote.

The ACP peer records delivery; it does not run an LLM. Native Claude, Codex,
OpenCode and Cursor turns, remote hosts, Windows/Linux, the full Rust test
suite and the browser end-to-end suite were not exercised in this experiment.
