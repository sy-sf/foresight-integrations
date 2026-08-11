# Changelog

## 1.0.0

- Rebranded the distributable plugin as `foresight-memory@foresight`.
- Added the missing Claude Code MCP server registration.
- Required a deployed Foresight API and a personal `hsk_` API key.
- Removed automatic startup of the incompatible public `hindsight-embed`
  package.
- Added personal knowledge and organization solution loading.
- Added progressive solution candidates and opened-solution trajectory metadata.
- Changed retention to idempotent full-session document snapshots with explicit
  compaction segments.
- Added Windows virtual-environment discovery and isolated MCP startup from
  project-local `.env` files.
- Pinned the MCP Python dependency to the release-tested version.
