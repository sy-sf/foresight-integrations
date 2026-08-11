# Foresight Integrations

Public integrations that connect supported agents and developer tools to the
hosted Foresight knowledge service.

Each integration lives in its own directory under `foresight-integrations/`
with its runtime, configuration, documentation, and tests. Claude Code plugins
are published through the marketplace manifest at
`.claude-plugin/marketplace.json`.

## Claude Code

Install the marketplace and the Foresight memory plugin for the current OS
user:

```bash
claude plugin marketplace add sy-sf/foresight-integrations --scope user
claude plugin install foresight-memory@foresight --scope user
```

For a project-local evaluation, run the commands from that project and replace
`--scope user` with `--scope local` in both commands.

The plugin connects to `https://daytonaio.39on.com` and requires a personal
Foresight API key beginning with `hsk_`. See
[`foresight-integrations/claude-code/README.md`](./foresight-integrations/claude-code/README.md)
for configuration, verification, update, and removal instructions.

## Repository layout

```text
.claude-plugin/marketplace.json
foresight-integrations/
└── claude-code/
```

Additional integrations can be added as sibling directories. Only integrations
that are installable as Claude Code plugins should be added to the marketplace
manifest.
