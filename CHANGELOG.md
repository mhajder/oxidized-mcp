## v0.2.0 (2026-10-19)

### Feat

- **transport**: accept streamable-http as an alias for http
- add Python 3.15 support
- **health**: serve /health and probe it from the Docker healthcheck

### Fix

- **config**: keep OXIDIZED_URL credentials out of logs, reject unusable URLs
- **nodes**: treat blank filters and missing statuses consistently
- **sentry**: read SENTRY_PROFILE_LIFECYCLE case-insensitively and warn on typos
- **versions**: page long diffs, count banner lines, reject blank oids
- **search**: report unaddressable nodes instead of failing the search
- **transport**: stop on an unknown MCP_TRANSPORT instead of running stdio
- **transport**: ignore surrounding spaces in MCP_TRANSPORT
- **transport**: bind loopback when MCP_HTTP_HOST is blank
- **config**: validate settings of enabled features only
- **config**: treat blank env values as unset
- **config**: reject unrecognized boolean settings
- **sentry**: log the enabled message by initialising after logging
- **docker**: run as numeric UID 10001 with root-owned app files

### Refactor

- **server**: read config attributes directly

## v0.1.0 (2026-09-26)

### Feat

- initialize Oxidized MCP Server project
