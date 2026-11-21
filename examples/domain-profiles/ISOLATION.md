# Instance isolation: shared sign-in, private working state

Decision: instances may use the same Claude/Codex account. Settings, memories,
skills, plugins, transcripts and working state belong to each instance.
This is configuration isolation, not an OS sandbox: an agent with permission
to read a shared source can still read it deliberately.

## What was tested

On macOS 15.6.1, two real native OpenResearch windows opened on separate
servers, projects and synthetic chats. Their API settings differed, and
neither chat list contained the other's session. Content's theme was changed
to Light; Atlas retained its own preference and was then set to Dark.
Reopening Content preserved its Light preference and selected project/chat.

The native WebKit store was also tested independently of server ports: the
same executable, bundle identifier and HTTP origin ran as A, then B, then A,
then B, using two persistent store UUIDs. Cookies, localStorage and IndexedDB
were initially empty in both stores; reopening each returned only its own
marker. These checks used a local fixture, not an LLM.

Reproduce after building with `python3 scripts/desktop-isolation-probe.py`.
It first checks the native browser stores, then leaves two synthetic app
windows open. Type `check` for API state separation, `restart-content` to
reopen that window, and `stop` to close the test windows and servers. The
script prints its temporary evidence directory. Settings can be changed in
these disposable windows to verify that they stay independent.

The opt-in native window boundary is implemented for macOS 14+:

- `ORX_DESKTOP_INSTANCE_ID` is a nonzero UUID assigned once to an instance.
  Persist it in the future launcher's registry, across process restarts.
  Never reuse it for another instance or derive it from the domain name.
- `ORX_DESKTOP_INSTANCE_LABEL` prefixes the native window title.
- Custom-profile desktop windows require the UUID. Older macOS versions
  reject this mode instead of falling back to a shared store.
- The existing `WKWebViewConfiguration` gets its own `WKWebsiteDataStore`.
  Setting only wry's builder identifier would not work here: wry uses the
  store of the supplied configuration instead.
- Standard desktop mode without an instance ID retains its existing store.

The experiment used the debug `--dev-desktop <port>` entry point with
separately launched servers. It does not establish Finder multi-instance
launching, Windows/Linux single-instance behavior or production packaging.
Keep an instance's HTTP origin stable as well as its browser-store UUID:
changing its port changes the origin of localStorage and IndexedDB.

## Remaining sharing paths found in the source

The native window fix does **not** isolate every agent setting. The following
are source findings and implementation requirements, not completed features.

| Owner | Current coupling | Required boundary |
| --- | --- | --- |
| `src/local/native_store.rs`, `prepare_claude_links` | Links `settings.json`, `settings.local.json`, `CLAUDE.md`, `plugins`, `skills`, plus account files from the user's Claude home. | Private configuration home; link only the credential material needed by the selected account. Keep the existing secure-storage account namespace separate from the settings home. |
| Same file, `prepare_codex_links` | Links almost every child of the user's Codex home except a denylist of session/runtime files. This includes configuration and customizations. | Replace that broad sharing policy with an explicit auth-only path for isolated instances. Their `config.toml`, instructions, skills and plugin state must be local files. |
| `src/local/agent_profiles.rs`, `prepare_home_in` | Additional account profiles also call these linkers. Claude additionally synchronizes `mcpServers` from the main account file. | Apply the same split to named accounts; stop synchronizing global MCP settings into them. Preserve only required identity/auth fields. |
| `src/local/harness/codex.rs`, `native_skill_roots`, `plugin_skills_dirs`; `src/local/user_skills.rs`, `discover_mirrored` | Discover global `~/.agents/skills`, legacy Codex skills and plugins separately from the isolated transcript home. | Point discovery at instance-owned roots. Verify the native CLI itself also excludes global discovery; merely hiding skills in the ORX picker is insufficient. |
| `src/config.rs`, synced-env read/write/remove; `src/local/chat/mod.rs`, `prepare_env` | Settings → Environment reads/writes `~/.openresearch/env`; every child can receive this shared file. It is outside both the data directory and XDG config directory. | One env file per instance, consistently used by readers, writers, shell hooks and child preparation. Account auth must be resolved separately, not by importing the entire global env file. |
| `src/local/shell_env.rs`, `var`; `src/commands/app.rs`, `hydrate_shell_env` | A desktop login-shell probe can override explicitly supplied data/config/cache variables. | A supervised instance's supplied locations must win. Import only the missing tool-discovery values the launcher allows; do not let shell startup retarget an active instance. |
| `src/local/native_store.rs`, session lookup; `src/local/native_chats.rs` | Native session recovery can search the user's legacy stores; importing a native chat can retain its original store. | Instance chat execution must resume only instance-owned native sessions. A deliberate import must copy/rehydrate into the instance or remain an explicitly external read-only source. |
| OpenCode, Cursor and generic ACP adapters | Their configuration discovery and sign-in mechanisms differ; ACP has no common isolated-home contract. | Separate adapter capabilities. Do not advertise complete isolation for an adapter until its configuration, memory, skills and resume behavior have been tested. |

Project files such as `AGENTS.md`, `CLAUDE.md`, `.mcp.json` and native project
settings are another input. They may be intentionally shared source material,
but automatic customization import must be an explicit instance policy.
Disabling global settings while silently loading equivalent project or parent
directory settings would not establish the intended boundary.

## Implementation contract

The supervisor should resolve two independent objects before spawning:

1. **Account reference:** provider, account identity and access to the live
   credential owner. Reuse the live sign-in, rather than copying an OAuth
   token snapshot which may become stale after refresh. Where a provider
   cannot separate auth from configuration, use an adapter-specific bridge
   or report that mode as unsupported.
2. **Instance workspace:** stable instance UUID, pinned domain version, private
   ORX data/config/cache/env, private agent configuration and session homes,
   private skill/plugin/memory roots, stable origin and native browser-store
   UUID. Several instances of the same domain still get distinct workspaces.

Auth-only sharing needs an allowlist, not the current "share everything except
history" denylist. Account removal/sign-out is a shared-account operation;
closing an instance must not sign out the shared account. Token refresh must
continue to reach the account owner.

Start this mode with fresh instance directories. Existing managed links must
be explicitly detached or the migration refused; skipping link creation does
not remove links left by earlier launches. Include the policy/version in the
instance binding so a restart cannot silently change its meaning.

Apply the boundary to normal turns, one-shots (titles/reviews), delegates,
CLI fallbacks, detection and skill discovery. Test restart and native-session
recovery too. Do not globally disable all skills as a shortcut: the selected
domain's skills still need to load. The installed Claude CLI's `--bare` also
disables OAuth/keychain reads, so it is not a substitute for shared sign-in
with private configuration.

## Acceptance checks for the remaining agent layer

Give A and B different sentinel settings, instructions, skills, plugins,
memory and env values, with a third sentinel in the user's global profile.
Through each real adapter's inspection API and a bounded real turn, verify
that A receives A's material only, B receives B's only, and both identify the
intended shared account. Mutate A, restart both, and repeat. Confirm that
native-session recovery does not resume B or a legacy user session. Exercise
auth refresh and prove neither instance overwrites the other's state.

These agent-layer acceptance checks have **not** been run. The current fork
proves separate servers and native browser stores, not complete agent
configuration isolation.
