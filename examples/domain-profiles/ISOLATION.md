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
- Custom-profile and private-settings desktop windows require the UUID. Older macOS versions
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

## Private agent settings mode

The fork now has an opt-in implementation for the default Claude Code and
Codex accounts:

```sh
ORX_PRIVATE_AGENT_SETTINGS=1 \
ORX_AGENT_PROFILE="/absolute/path/to/content/profile.toml" \
ORX_DATA_DIR="/absolute/path/to/instances/content-a" \
target/debug/orx up --no-browser --no-telemetry
```

Use a different absolute data directory for each instance, including two
instances of the same profile. ORX config, cache and its saved environment
file live at `config/`, `cache/` and `env` inside that directory. The native
homes remain `agents/claude` and `agents/codex`. A persistent
`agent-settings-policy.json` binding rejects an old data directory and a
restart with private mode removed. Private mode never attempts to adopt old
managed links. The ordinary mode is unchanged.

| Boundary | Implemented behavior |
| --- | --- |
| Claude configuration | Private settings, skills, plugins, history and `.claude.json`; no global MCP/project-state copy. The shared account's secure-storage namespace is passed separately. The file credential fallback is the only linked file. |
| Codex configuration | Only `auth.json` is linked to the selected default account owner; no global config, skills, plugins or memory links. Native app-server uses file credential storage. File links are required; no hard-link/copy fallback pretending to share refreshed credentials. |
| Credential conflict | An unexpectedly replaced credential bridge or inherited settings link causes an error. Private files are never adopted back into the account owner's configuration. |
| Codex skill discovery | Its native loader independently scans the OS user's `.agents/skills`. Before each normal turn, ORX lists skills, disables enabled paths outside the canonical instance data directory through `skills/config/write`, then lists again. Unknown/error/remaining external skills stop the turn. Without a domain profile, instance skills stay enabled. With a profile, only its declared skill files in the current worktree are allowed; unrelated files inside the instance are disabled too. The one-shot path is inspected too. |
| Claude launch | Uses private user settings and explicit MCP configuration; project/local settings are excluded from settings-source selection. Detection and model-catalog probes also use the private config home with the shared sign-in namespace. Custom profiles disable native skill/command auto-loading and supply their explicit catalog through the playbook. |
| ORX skill picker | Custom profiles do not discover/import uploaded, project, native or plugin skills. The profile is the complete catalog. Without a custom profile, private mode discovers only instance-owned Claude/Codex skills. |
| Environment | All ORX saved-env read/write/remove paths use the instance file. Agent preparation removes inherited provider-prefixed variables before applying instance variables. A desktop shell probe may update PATH but cannot retarget instance directories. |
| History | Native resume lookup does not search legacy user homes. Legacy execution is rejected; native chat import is unavailable in this mode. A future shared-session source must be an explicit reader, not legacy resume. |
| Unsupported adapters | The mode exposes Claude/Codex only. Additional account profiles and legacy Codex exec chat are rejected. Ordinary mode retains their existing behavior. |

The filtering is a pre-turn configuration check, not a filesystem security
boundary. Native Codex still inspects external skill metadata to enumerate and
disable it. Changes made outside ORX during an active turn are not isolated by
an OS namespace. Explicit source-file reads remain possible.

## Native checks performed

On macOS, Codex CLI **0.171.0** was exercised through the production ORX spawn
and JSON-RPC code using synthetic accounts only. Two independent configuration
homes reported different reasoning settings (`low` and `high`). In each:

- a global sentinel skill was initially enabled despite the separate home;
- ORX's native skill check disabled that sentinel;
- an instance-owned sentinel skill remained enabled;
- `account/login/start` with a synthetic API key wrote through the credential
  bridge to the account owner, and the link survived;
- re-preparing the same home preserved its private configuration.

Claude Code **2.1.329** was exercised through ORX's production control-request
path and an isolated test credential namespace. Its initialization response
exposed the private sentinel skill and omitted the global sentinel skill.
Neither native probe made a model request. These are real CLI configuration
checks, not a simulated agent or an OAuth refresh test.

Reproduce after building the tests (binary locations are machine-specific):

```sh
ORX_TEST_CODEX_BINARY="/absolute/path/to/codex" \
ORX_TEST_CLAUDE_BINARY="/absolute/path/to/claude" \
cargo test --locked --bin orx private_settings_real_ -- --ignored
```

Optional `ORX_ISOLATION_REPORT_FILE` and
`ORX_CLAUDE_ISOLATION_REPORT_FILE` save the respective sanitized JSON reports.
The tests run inside existing ORX temporary-home fixtures and never use real
account credentials. Module tests also cover stale-policy rejection, atomic
credential replacement by the owner, private env read/write/remove, exclusion
of inherited provider variables and legacy-session lookup.

## Closed profile skill sets

A profile is the source of its skills, not a filter applied to a discovered
collection. The profile-only native probes exercise the stricter policy:
Codex keeps the declared `orx-agent-delegation` skill enabled while disabling
both a global sentinel and an unlisted sentinel inside the instance. Claude's
native skill auto-loading is off entirely; the selected catalog and file
paths are rendered explicitly into the profile playbook instead. Module tests
check that catalog, exclusion of uploaded skills without deleting the upload,
and refusal to resume data bound under the older skill policy.

```sh
ORX_TEST_CODEX_BINARY="/absolute/path/to/codex" \
ORX_TEST_CLAUDE_BINARY="/absolute/path/to/claude" \
cargo test --locked --bin orx profile_skills_real_ -- --ignored
```

`ORX_PROFILE_ISOLATION_REPORT_FILE` and `ORX_CLAUDE_PROFILE_REPORT_FILE`
optionally save sanitized reports. These checks make no model requests.

## Remaining acceptance work

This mode is an experimental configuration boundary, not yet a complete
multi-instance product. Still unverified or unimplemented:

- A normal model turn, restart/resume and parallel OAuth refresh using the
  real shared accounts. The synthetic credential-write check does not prove
  concurrent OAuth refresh or macOS Keychain behavior.
- Codex accounts stored exclusively in the OS keyring. The current bridge
  supports the `auth.json` account owner; a keyring-only account needs a
  separate credential adapter.
- Full exclusion of parent-directory instructions, enterprise-managed
  configuration and shell startup files. Settings-source selection and
  private homes are not proof that every native instruction source is gone.
- A supervisor with a deliberately selected inherited environment, stable
  port/UUID registry, lifecycle controls and recovery. Provider-variable
  filtering is not a complete environment allowlist.
- Production Finder multi-instance launching, Windows/Linux behavior and
  Windows file-symlink availability. Previous native-window tests used the
  debug desktop attachment entry point on macOS.

Do not label this mode complete isolation until those checks are performed.
The domain profile still changes guidance and bundled skills, not the research
schema or the UI. The shared session repository/Atlas remains a separate,
explicit source integration.
