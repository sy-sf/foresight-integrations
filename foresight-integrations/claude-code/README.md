# Foresight Memory for Claude Code

`foresight-memory` connects Claude Code to a deployed Foresight service. It
collects complete Claude Code session snapshots, recalls distilled observations
before each prompt, and lets Claude progressively open reusable solution
methodologies through MCP tools.

The hosted Foresight service is:

```text
https://daytonaio.39on.com
```

The plugin does not start a local service. Foresight's personal
knowledge, document snapshot, and solution APIs require a compatible deployed
Foresight backend and a personal `hsk_` API key issued by that same backend.

## Prerequisites

- Claude Code with Plugin Marketplace support
- Python 3
- Bash (`bash`; on Windows use an environment that provides it)
- Network access on first MCP startup to install the pinned Python dependency
- A Foresight account and personal API key

## 1. Create a Foresight account and API key

1. Open [daytonaio.39on.com](https://daytonaio.39on.com).
2. Sign in, or register when registration is enabled.
3. Open the avatar menu and select **API Key**.
4. Select **Create API Key**.
5. Copy the complete `hsk_` key immediately. It is shown only once.

Do not commit the key or place it in a shared Claude settings file.

## 2. Install from the public GitHub marketplace

```bash
claude plugin marketplace add sy-sf/foresight-integrations --scope user

claude plugin install foresight-memory@foresight --scope user
```

The repository is public, so users do not need Foresight source-code access or
GitLab credentials. The default `main` branch is used automatically. Verify
anonymous access with:

```bash
git ls-remote https://github.com/sy-sf/foresight-integrations.git HEAD
```

## 3. Choose plugin installation scope

Marketplace scope and plugin scope are separate commands. Use the same scope for
both unless there is a deliberate administrative reason not to.

| Desired effect | Scope | Stored for | Recommended use |
| --- | --- | --- | --- |
| All projects for the current OS user | `user` | One user, every project | Most individual users |
| Everyone who clones one repository | `project` | Shared project settings | Team-managed repositories |
| Only this user in one repository | `local` | Local project settings | Evaluation or private opt-in |

### User scope: all projects

Run from any directory:

```bash
claude plugin marketplace add sy-sf/foresight-integrations --scope user
claude plugin install foresight-memory@foresight --scope user
```

The plugin becomes available in every Claude Code project for this OS user. If
the connection is also configured globally, Foresight can recall and retain in
every project where the plugin remains enabled.

### Project scope: shared with the repository

Run from the repository root:

```bash
claude plugin marketplace add sy-sf/foresight-integrations --scope project
claude plugin install foresight-memory@foresight --scope project
```

Project scope is intended to be shared through the repository's Claude settings.
Every collaborator must use their own personal Foresight API key. Never commit
one shared key.

### Local scope: one user in one project

Run from the repository root:

```bash
claude plugin marketplace add sy-sf/foresight-integrations --scope local
claude plugin install foresight-memory@foresight --scope local
```

Local scope affects only the current user in the current project and is the
safest choice for trying the plugin without changing team settings.

## 4. Configure the server and credential

Plugin installation scope answers “where is the plugin enabled?” Configuration
scope answers “where do this URL and key apply?” They do not have to be the same.

Settings are loaded in this order, with later values winning:

1. Built-in defaults
2. `~/.foresight/claude-code.json`
3. Environment variables, including Claude project `env` settings

The plugin does not ship a second copy of its defaults in `settings.json`.

### Global connection for all projects

Create or merge `~/.foresight/claude-code.json`:

```json
{
  "foresightApiUrl": "https://daytonaio.39on.com",
  "foresightApiKey": "hsk_replace_with_your_key"
}
```

Protect the file on Unix-like systems:

```bash
chmod 600 ~/.foresight/claude-code.json
```

This is the recommended pairing with a user-scope plugin installation.

### Private connection for one local project

Create or merge `.claude/settings.local.json` in the project:

```json
{
  "env": {
    "FORESIGHT_API_URL": "https://daytonaio.39on.com",
    "FORESIGHT_API_KEY": "hsk_replace_with_your_key"
  }
}
```

Confirm this file is ignored by Git. Environment variables override the global
configuration file, so a project can select a different Foresight deployment.
The API key must come from the same deployment as the URL.

### Shared project URL with per-user credentials

A team may commit only the non-secret URL in `.claude/settings.json`:

```json
{
  "env": {
    "FORESIGHT_API_URL": "https://daytonaio.39on.com"
  }
}
```

Each collaborator then stores their personal key in
`~/.foresight/claude-code.json` or their own `.claude/settings.local.json`.

## 5. Verify

An optional standalone `foresight-setup` Skill is distributed separately as a
ZIP. It can bootstrap the marketplace and plugin, ask for missing configuration,
and run credential-redacting diagnostics before this plugin is installed.

Manual verification:

```bash
claude plugin list
claude plugin details foresight-memory@foresight
```

The details output should report:

```text
Hooks (4)  SessionStart, UserPromptSubmit, Stop, SessionEnd
MCP servers (1)  foresight
```

If installation occurs during an interactive Claude Code session, run
`/reload-plugins` or start a new session.

For an end-to-end test, complete a short conversation and exit Claude normally
so the `SessionEnd` hook submits the final snapshot. Document ingestion may be
visible before background knowledge distillation completes.

## Behavior

- `SessionStart` validates required configuration and injects the progressive
  solution-loading protocol.
- `UserPromptSubmit` recalls observations and lightweight solution candidates.
- Claude can call `agent_knowledge_open_solution` to load a full methodology.
- Every `Stop` upserts the latest complete structured session snapshot.
- `SessionEnd` sends a final idempotent snapshot without bypassing the server's
  idle window.
- Each changed snapshot resets server-side idle processing; knowledge extraction
  starts only after the configured idle period passes without another update.
- Opened solutions are attached to trajectory metadata so Foresight can learn
  which methodologies were actually used.

All authenticated users write to their own personal knowledge space. The plugin
does not expose configurable Bank IDs or project/session-specific Banks.

## Configuration reference

| Setting | Environment variable | Default | Purpose |
| --- | --- | --- | --- |
| `foresightApiUrl` | `FORESIGHT_API_URL` | required | Foresight API base URL |
| `foresightApiKey` | `FORESIGHT_API_KEY` | required | Personal `hsk_` API key |
| `autoRecall` | `FORESIGHT_AUTO_RECALL` | `true` | Recall before user prompts |
| `autoRetain` | `FORESIGHT_AUTO_RETAIN` | `true` | Persist session snapshots |
| `recallBudget` | `FORESIGHT_RECALL_BUDGET` | `mid` | Recall effort/latency tradeoff |
| `recallMaxTokens` | `FORESIGHT_RECALL_MAX_TOKENS` | `1024` | Recall token budget |
| `recallTypes` | — | `["observation", "solution"]` | Knowledge types included in automatic recall |
| `recallSolutionDetail` | `FORESIGHT_RECALL_SOLUTION_DETAIL` | `candidate` | Auto-recall solution projection |
| `recallContextTurns` | `FORESIGHT_RECALL_CONTEXT_TURNS` | `1` | Number of recent user turns used to compose the recall query |
| `recallMaxQueryChars` | `FORESIGHT_RECALL_MAX_QUERY_CHARS` | `800` | Maximum recall-query length |
| `recallRoles` | — | `["user", "assistant"]` | Roles included when multi-turn recall is enabled |
| `recallPromptPreamble` | — | built-in Chinese prompt | Text placed before recalled knowledge |
| `requestTimeoutSeconds` | `FORESIGHT_REQUEST_TIMEOUT_SECONDS` | per operation | HTTP timeout override |
| `personalKnowledgeMission` | `FORESIGHT_PERSONAL_KNOWLEDGE_MISSION` | empty | Optional knowledge mission |
| `retainMission` | — | unset | Optional extraction mission, applied with `personalKnowledgeMission` |
| `enableKnowledgeTools` | — | `true` | Expose Foresight MCP knowledge and solution tools |
| `debug` | `FORESIGHT_DEBUG` | `false` | Verbose `[Foresight]` logs |

Only `foresightApiUrl` and `foresightApiKey` are required user configuration.
All other settings are optional overrides of built-in defaults. Session snapshot
cadence, chunking, tags, context, and processing timing are implementation or
server concerns and are intentionally not configurable by this plugin.

## Update

```bash
claude plugin marketplace update foresight
claude plugin update foresight-memory@foresight
```

Restart Claude Code or run `/reload-plugins` after an update.

## Remove

Use the scope originally selected during installation:

```bash
claude plugin uninstall foresight-memory@foresight --scope user
claude plugin marketplace remove foresight
```

Removing the local plugin does not delete knowledge already persisted in the
authenticated user's Foresight knowledge space.

## Troubleshooting

- **Marketplace clone fails:** verify access to GitHub and run `git ls-remote
  https://github.com/sy-sf/foresight-integrations.git HEAD`.
- **MCP server missing:** update the marketplace and plugin, then restart Claude
  Code. `plugin details` must show one `foresight` MCP server.
- **Missing URL:** configure `foresightApiUrl` or `FORESIGHT_API_URL`.
- **Authentication failure:** create a current `hsk_` key on the same server as
  the configured URL.
- **No retained data:** complete a response and exit Claude normally; set
  `FORESIGHT_DEBUG=true` and inspect `[Foresight]` hook errors.
- **No recalled knowledge:** first confirm retention, then allow the backend
  worker to finish processing the submitted document.
- **Old upstream plugin also installed:** disable or uninstall it to avoid
  sending each session to two systems:

  ```bash
  claude plugin disable hindsight-memory@hindsight
  ```
