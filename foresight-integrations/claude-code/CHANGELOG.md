# Changelog

## 1.1.0

- Upsert the latest complete Claude session snapshot after every completed
  response.
- Delegate idle debouncing and knowledge extraction timing entirely to the
  Foresight server, including final `SessionEnd` snapshots.
- Remove legacy turn-count, chunking, transcript-filter, tag, context, and
  metadata configuration.
- Remove the duplicate packaged `settings.json`; built-in defaults now have one
  source of truth, and users only need to configure the API URL and key.
- Move user configuration to `~/.foresight/claude-code.json` and rename
  plugin-owned settings and environment variables to the Foresight namespace.

## 1.0.0

- Rebranded the distributable plugin as `foresight-memory@foresight`.
- Added the missing Claude Code MCP server registration.
- Required a deployed Foresight API and a personal `hsk_` API key.
- Removed automatic startup of an incompatible local embedded service.
- Added personal knowledge and organization solution loading.
- Added progressive solution candidates and opened-solution trajectory metadata.
- Changed retention to idempotent full-session document snapshots with explicit
  compaction segments.
- Added Windows virtual-environment discovery and isolated MCP startup from
  project-local `.env` files.
- Pinned the MCP Python dependency to the release-tested version.
