# Oxidized MCP Server

<!-- mcp-name: io.github.mhajder/oxidized-mcp -->

Oxidized MCP Server is a Python-based Model Context Protocol (MCP) server that gives AI assistants access to [Oxidized](https://github.com/ytti/oxidized), the network device configuration backup tool. It talks to the [oxidized-web](https://github.com/ytti/oxidized-web) REST API to list devices, check backup health, read and search device configurations, browse and diff configuration history, and queue backups. It supports read-only mode, tag-based tool filtering, and bearer token authentication for HTTP transports.

## Features

### Core Features

- List and filter managed devices by group, model and last backup status
- Find devices by partial name, full name (`group/name`) or IP address
- Summarise backup health: failures, devices never backed up and stale backups
- Read the latest backed-up configuration of any device, paged for large configs
- Search all configurations for a regular expression or literal text, with matching lines and context

### Configuration History

- List the stored configuration versions of a device (git output)
- Read a device's configuration as it was at any version (full oid or short prefix)
- Diff two versions, or just see the most recent change, as a unified diff

### Operations

- Queue an immediate backup of a device, optionally with a commit message and author
- Reload the node list from its source (router.db, SQL, HTTP, ...)
- Read-only mode hides every operation for safe monitoring

### Advanced Capabilities

- Node vars (often device credentials) are redacted in tool output by default
- HTTP Basic auth for oxidized-web behind a reverse proxy
- Rate limiting, SSL/TLS verification and configurable timeouts
- Tag-based tool filtering and an optional tool-search transform
- Bearer token authentication for HTTP transports

## Installation

### Prerequisites

- Python 3.11 to 3.15
- A running Oxidized instance with [oxidized-web](https://github.com/ytti/oxidized-web) enabled
- For version history and diffs: the Oxidized `git` (or `gitcrypt`) output

### Quick Install from PyPI

The easiest way to get started is to install from PyPI:

```sh
# Using UV (recommended)
uvx oxidized-mcp

# Or using pip
pip install oxidized-mcp
```

Remember to configure the environment variables for your Oxidized instance before running the server:

```sh
# Create environment configuration
export OXIDIZED_URL=http://localhost:8888
```

For more details, visit: https://pypi.org/project/oxidized-mcp/

### Install from Source

1. Clone the repository:

```sh
git clone https://github.com/mhajder/oxidized-mcp.git
cd oxidized-mcp
```

2. Install dependencies:

```sh
# Using UV (recommended)
uv sync

# Or using pip
pip install -e .
```

3. Configure environment variables:

```sh
cp .env.example .env
# Edit .env with your Oxidized URL (and Basic auth credentials if needed)
```

4. Run the server:

```sh
# Using UV (recommended)
uv run oxidized-mcp

# Or using the installed command directly
oxidized-mcp
```

### Development Setup

For development with additional tools:

```sh
# Clone and install with development dependencies
git clone https://github.com/mhajder/oxidized-mcp.git
cd oxidized-mcp
uv sync --group dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/

# Run linting and formatting
uv run ruff check .
uv run ruff format .

# Run type checking
uv run ty check .

# Setup prek hooks
uv run prek install
```

## Configuration

### Environment Variables

```env
# Oxidized Connection Details
# Base URL of oxidized-web (include the url_prefix if one is configured).
# A query string (?...) is rejected; use the Basic auth settings below, or
# percent-encode special characters in credentials put in the URL.
OXIDIZED_URL=http://localhost:8888

# Optional HTTP Basic auth, e.g. for a reverse proxy in front of oxidized-web
# OXIDIZED_USERNAME=
# OXIDIZED_PASSWORD=

# SSL Configuration
OXIDIZED_VERIFY_SSL=true
OXIDIZED_TIMEOUT=30
# Timeout for search_configs (oxidized-web reads every configuration)
OXIDIZED_SEARCH_TIMEOUT=300

# Redact node vars (often device credentials) in tool output
OXIDIZED_REDACT_NODE_VARS=true

# Read-Only Mode
# Set READ_ONLY_MODE true to disable operations (trigger_node_backup, reload_nodes)
READ_ONLY_MODE=false

# Disabled Tags
# Comma-separated list of tags to disable tools for (empty by default)
# Example: OXIDIZED_DISABLED_TAGS=search,diff
OXIDIZED_DISABLED_TAGS=

# Logging Configuration
LOG_LEVEL=INFO

# Rate Limiting (requests per minute)
# Set RATE_LIMIT_ENABLED true to enable rate limiting
RATE_LIMIT_ENABLED=false
RATE_LIMIT_MAX_REQUESTS=60
RATE_LIMIT_WINDOW_MINUTES=1

# Tool Search Transform (Optional)
# Set TOOL_SEARCH_ENABLED true to replace full tool listings with search_tools + call_tool
TOOL_SEARCH_ENABLED=false
# Search strategy: bm25 (natural language) or regex (pattern match)
TOOL_SEARCH_STRATEGY=bm25
# Maximum number of tools returned by search_tools
TOOL_SEARCH_MAX_RESULTS=5

# Sentry Error Tracking (Optional)
# Set SENTRY_DSN to enable error tracking and performance monitoring
# SENTRY_DSN=https://your-key@o12345.ingest.us.sentry.io/6789
# Optional Sentry configuration
# SENTRY_TRACES_SAMPLE_RATE=1.0
# SENTRY_SEND_DEFAULT_PII=false
# SENTRY_ENVIRONMENT=production
# SENTRY_RELEASE=1.2.3
# SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0
# SENTRY_PROFILE_LIFECYCLE=trace
# SENTRY_ENABLE_LOGS=true

# MCP Transport Configuration
# Transport type: 'stdio' (default), 'sse' (Server-Sent Events), or 'http' (HTTP Streamable)
# 'streamable-http' is accepted as an alias for 'http'. Any other value stops
# the server at startup.
# MCP_TRANSPORT=stdio

# HTTP Transport Settings (used when MCP_TRANSPORT=sse or MCP_TRANSPORT=http)
# Host to bind the HTTP server (default: 127.0.0.1)
# MCP_HTTP_HOST=127.0.0.1
# Port to bind the HTTP server (default: 8000)
# MCP_HTTP_PORT=8000
# Optional bearer token for authentication (leave empty for no auth)
# MCP_HTTP_BEARER_TOKEN=
```

### Sentry Error Tracking & Monitoring (Optional)

The server optionally supports **Sentry** for error tracking, performance monitoring, and debugging. Sentry integration is completely optional and only initialized if configured.

#### Installation

To enable Sentry monitoring, install the optional dependency:

```sh
# Using UV (recommended)
uv sync --extra sentry
```

#### Configuration

Enable Sentry by setting the `SENTRY_DSN` environment variable in your `.env` file:

```env
# Required: Sentry DSN for your project
SENTRY_DSN=https://your-key@o12345.ingest.us.sentry.io/6789

# Optional: Performance monitoring sample rate (0.0-1.0, default: 1.0)
SENTRY_TRACES_SAMPLE_RATE=1.0

# Optional: Include personally identifiable information (default: false).
# Keep it off: with the MCP integration it records tool results, which are
# full device configurations including secrets.
SENTRY_SEND_DEFAULT_PII=false

# Optional: Environment name (e.g., "production", "staging")
SENTRY_ENVIRONMENT=production

# Optional: Release version (auto-detected from package if not set)
SENTRY_RELEASE=1.2.2

# Optional: Profiling - continuous profiling sample rate (0.0-1.0, default: 1.0)
SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0

# Optional: Profiling - lifecycle mode for profiling (default: "trace")
# Options: "all", "continuation", "trace"
SENTRY_PROFILE_LIFECYCLE=trace

# Optional: Enable log capture as breadcrumbs and events (default: true)
SENTRY_ENABLE_LOGS=true
```

#### Features

When enabled, Sentry automatically captures:

- **Exceptions & Errors**: All unhandled exceptions with full context
- **Performance Metrics**: Request/response times and traces
- **MCP Integration**: Detailed MCP server activity and interactions
- **Logs & Breadcrumbs**: Application logs and event trails for debugging
- **Context Data**: Environment, client info, and request parameters

#### Getting a Sentry DSN

1. Create a free account at [sentry.io](https://sentry.io)
2. Create a new Python project
3. Copy your DSN from the project settings
4. Set it in your `.env` file

#### Disabling Sentry

Sentry is completely optional. If you don't set `SENTRY_DSN`, the server will run normally without any Sentry integration, and no monitoring data will be collected.

## Available Tools

### Node Tools

- `list_nodes`: List devices with optional filters (group, model, last run status, name/IP substring) and paging; group/model are filtered by Oxidized itself (`/nodes/<group|model>/<value>.json`), so large inventories are not transferred in full
- `get_node`: Get a single device's details and last backup run (accepts name or IP)
- `find_nodes`: Find devices by partial name, full name or IP, exact matches first
- `get_backup_stats`: Backup health summary - counts per status, group and model, success rate, failed / never backed up / stale devices; optionally for a single group or model

### Configuration Tools

- `get_node_config`: Get the latest backed-up configuration of a device (by name or IP), paged by lines (`offset` / `max_lines`)
- `search_configs`: Search all configurations for a regex or literal text; returns matching lines with line numbers and optional context

### Version History Tools

Require the Oxidized `git` or `gitcrypt` output.

- `list_node_versions`: List stored configuration versions (oid, date, author, message), newest first
- `get_node_version`: Get the configuration at a given version (full oid or unique prefix), paged by lines
- `diff_node_versions`: Unified diff between two versions; defaults to the most recent change

### Operation Tools

Hidden when `READ_ONLY_MODE=true`.

- `trigger_node_backup`: Queue an immediate backup of a device, optionally recording a commit message and author
- `reload_nodes`: Reload the whole node list from its source (a per-node reload is deliberately not offered: oxidized-web's `/reload?node=X` replaces the entire in-memory node list with the matching nodes)

## Security & Safety Features

### Read-Only Mode

The server supports a read-only mode that disables all write operations for safe monitoring:

```env
READ_ONLY_MODE=true
```

When enabled, only tools tagged `read-only` are exposed: `trigger_node_backup` and `reload_nodes` are hidden, while every node, configuration and history tool stays available.

### Tag-Based Tool Filtering

You can disable specific categories of tools by setting disabled tags:

```env
OXIDIZED_DISABLED_TAGS=search,diff
```

Available tags include:
- `node` - Node listing and lookup tools (and the backup/reload operations)
- `stats` - Backup health statistics
- `config` - Configuration read tools
- `search` - Configuration search and node search tools
- `version` - Version history tools
- `diff` - Version diff tool
- `backup` - Backup trigger operation
- `reload` - Node list reload operation
- `read-only` - Every tool that does not change Oxidized state

### Rate Limiting

The server supports rate limiting to control API usage and prevent abuse. If enabled, requests are limited per client using a sliding window algorithm.

Enable rate limiting by setting the following environment variables in your `.env` file:

```env
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=60    # Maximum requests allowed per window
RATE_LIMIT_WINDOW_MINUTES=1   # Window size in minutes
```

If `RATE_LIMIT_ENABLED` is set to `true`, the server will apply rate limiting middleware. Adjust `RATE_LIMIT_MAX_REQUESTS` and `RATE_LIMIT_WINDOW_MINUTES` as needed for your environment. Both must be at least 1 when rate limiting is on; while it is off they are not read.

### Tool Search for Large Toolsets

FastMCP tool search can reduce prompt size for servers with many tools.
When enabled, `list_tools` returns two synthetic tools:

- `search_tools`: Finds matching tools and returns their full schemas
- `call_tool`: Executes any discovered tool by name

Enable it with:

```env
TOOL_SEARCH_ENABLED=true
TOOL_SEARCH_STRATEGY=bm25      # bm25 or regex
TOOL_SEARCH_MAX_RESULTS=8      # optional, default is 5
```

`bm25` supports natural language queries, while `regex` uses a regex
`pattern` input for deterministic matching.
With tool search enabled, any other `TOOL_SEARCH_STRATEGY` value stops the
server at startup, and `TOOL_SEARCH_MAX_RESULTS` must be at least 1.

Tool search respects existing visibility controls (read-only mode and
disabled tags).

### SSL/TLS Configuration

The server supports SSL certificate verification and custom timeout settings:

```env
OXIDIZED_VERIFY_SSL=true    # Enable SSL certificate verification
OXIDIZED_TIMEOUT=30         # Request timeout in seconds
```

### Upstream Authentication

oxidized-web has no authentication of its own and is usually published behind a reverse proxy with HTTP Basic auth. Set both variables to send Basic auth with every request:

```env
OXIDIZED_USERNAME=oxidized
OXIDIZED_PASSWORD=your-password
```

### Node Vars Redaction

Node vars from the Oxidized source (router.db, SQL, HTTP) often contain per-device credentials, and oxidized-web returns them unredacted from `/nodes.json`. The server replaces every var value with `<redacted>` (keeping the keys) unless you opt out:

```env
OXIDIZED_REDACT_NODE_VARS=false
```

### Transport Configuration

The server supports multiple transport protocols for different deployment scenarios:

#### STDIO Transport (Default)

The default transport uses standard input/output for communication. This is ideal for local usage and integration with tools that communicate via stdin/stdout:

```env
MCP_TRANSPORT=stdio
```

#### HTTP SSE Transport (Server-Sent Events)

For network-based deployments, you can use HTTP with Server-Sent Events. This allows the MCP server to be accessed over HTTP with real-time streaming:

```env
MCP_TRANSPORT=sse
MCP_HTTP_HOST=127.0.0.1        # Localhost
MCP_HTTP_PORT=8000           # Port to listen on
MCP_HTTP_BEARER_TOKEN=your-secret-token  # Optional authentication token
```

When using SSE transport with a bearer token, clients must include the token in their requests:

```bash
curl -H "Authorization: Bearer your-secret-token" http://localhost:8000/sse
```

#### HTTP Streamable Transport

The HTTP Streamable transport provides HTTP-based communication with request/response streaming. This is ideal for web integrations and tools that need HTTP endpoints:

```env
MCP_TRANSPORT=http
MCP_HTTP_HOST=127.0.0.1        # Localhost
MCP_HTTP_PORT=8000           # Port to listen on
MCP_HTTP_BEARER_TOKEN=your-secret-token  # Optional authentication token
```

When using streamable transport with a bearer token:

```sh
curl -H "Authorization: Bearer your-secret-token" \
     -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     http://localhost:8000/mcp
```

**Note**: The HTTP transport requires proper JSON-RPC formatting with `jsonrpc` and `id` fields. The server may also require session initialization for some operations.

#### Health Endpoint

Both HTTP transports (`sse` and `http`) serve `GET /health`, which answers `200 ok`
when the server is handling requests. It needs no bearer token and does not
contact Oxidized, so an Oxidized outage does not mark the MCP server unhealthy.
The Docker image's `HEALTHCHECK` uses it:

```sh
curl http://localhost:8000/health
```

## Oxidized Notes

- **Version history** (`list_node_versions`, `get_node_version`, `diff_node_versions`) needs the `git` or `gitcrypt` output. With the `file` output Oxidized reports no versions.
- **Groups**: node names should be unique. Tools accept an optional `group` to disambiguate; `get_node_config` and the history tools look the group up automatically when it is omitted (with the git output's `single_repo` the group is part of the stored path, so it is required by oxidized-web). Nodes without a group are shown as group `default`, and passing `default` back is treated as "no group". `search_configs` filters groups the same way as `list_nodes` (exact match). `trigger_node_backup` takes no group because Oxidized queues backups by node name or IP only.
- **Filtering large inventories**: `group` and `model` in `list_nodes` / `get_backup_stats` are filtered by Oxidized itself. The group must match exactly (case-sensitive, as shown by `list_nodes`), so a misspelt group returns nothing instead of downloading the whole inventory. Built-in model names are case-insensitive (`ios` and the `IOSXE` alias are sent as `IOS`, from a catalog of Oxidized's models); custom models must be spelled exactly. Neither filter ever falls back to downloading the whole inventory.
- **Search** runs in two steps: oxidized-web's `conf_search` finds matching devices (it reads every configuration server-side, so it can be slow on large installations), then only those configurations are fetched to extract matching lines. `max_nodes` caps the fetches; nodes that were never backed up (whose placeholder text can match) are skipped without counting. The server-side step uses `OXIDIZED_SEARCH_TIMEOUT` (default 300 s) instead of `OXIDIZED_TIMEOUT`. Patterns run as Ruby regexps on the server and Python regexps locally, line by line, so stick to common syntax.
- **Backups are asynchronous**: `trigger_node_backup` moves the device to the head of the queue. Check `get_node` for the result.
- **Errors**: oxidized-web answers unknown node names with HTTP 500. When it runs with `RACK_ENV=production` the reason is hidden, so a 500 on a node route is reported with a hint that it may be an unknown node name.

## Using Docker

A Docker image is available on GitHub Packages for easy deployment.

```sh
docker pull ghcr.io/mhajder/oxidized-mcp:latest

docker run --rm -p 8000:8000 \
  -e OXIDIZED_URL=http://oxidized:8888 \
  -e MCP_HTTP_BEARER_TOKEN=your-secret-token \
  ghcr.io/mhajder/oxidized-mcp:latest
```

The image defaults to the HTTP Streamable transport on port 8000 (`http://localhost:8000/mcp`).

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and ensure code quality (`uv run pytest && uv run ruff check .`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

MIT License - see LICENSE file for details.
