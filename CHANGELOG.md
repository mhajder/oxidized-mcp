# Changelog

## [0.2.2](https://github.com/mhajder/oxidized-mcp/compare/v0.2.1...v0.2.2) (2026-11-07)


### 🧩 CI

* pin actions to commit SHAs at latest versions ([e4d8f78](https://github.com/mhajder/oxidized-mcp/commit/e4d8f78124e13c2de9799f8b6d2c08c99a1f64b0))
* release with release-please ([43793d3](https://github.com/mhajder/oxidized-mcp/commit/43793d3cf81e093db071d3e26528bbf63275d3d0))
* replace softprops/action-gh-release with the gh CLI ([6c03741](https://github.com/mhajder/oxidized-mcp/commit/6c037417ce414f9437899e32a4788d84ea063a47))


### 📦 Build

* **deps-dev:** bump the uv-dev-dependencies group across 1 directory with 4 updates ([#22](https://github.com/mhajder/oxidized-mcp/issues/22)) ([c6ff940](https://github.com/mhajder/oxidized-mcp/commit/c6ff940043a541657b0fbfa335d8ed54ef66d212))
* **deps:** bump authlib from 1.8.0 to 1.9.1 ([#19](https://github.com/mhajder/oxidized-mcp/issues/19)) ([a2e0fab](https://github.com/mhajder/oxidized-mcp/commit/a2e0faba852a0793e46ef85a3a16487cf6b7dcda))
* **deps:** bump joserfc from 1.7.5 to 1.7.6 ([#20](https://github.com/mhajder/oxidized-mcp/issues/20)) ([1c41d4c](https://github.com/mhajder/oxidized-mcp/commit/1c41d4cfeb0fcbdd5f7a8f32b696efd759f2cec9))
* **deps:** bump the uv-dependencies group across 1 directory with 4 updates ([#16](https://github.com/mhajder/oxidized-mcp/issues/16)) ([9c1ee69](https://github.com/mhajder/oxidized-mcp/commit/9c1ee694d26e9f428d529ea483c869f650d461da))
* **deps:** bump ty to 0.0.98 and type check for all platforms ([51f36dd](https://github.com/mhajder/oxidized-mcp/commit/51f36ddf908cd9d3173fe23cea6a1e10fa636302))
* **deps:** raise minimum versions of dev tools and vulnerable dependencies ([a3df253](https://github.com/mhajder/oxidized-mcp/commit/a3df2530131c8649135947b2575fd5ee048d6f53))
* **deps:** stop Dependabot proposing Python pre-release and minor bumps ([28c9975](https://github.com/mhajder/oxidized-mcp/commit/28c9975cbf27e7ed966c937c2b16b02b8123350a))
* **docker:** pin base images by version and digest and update them with Dependabot ([94fed7c](https://github.com/mhajder/oxidized-mcp/commit/94fed7cd5d24e57591dc717b4e60568a8cd4e64b))
* install optional extras for ty check in pre-commit hook ([6468c1f](https://github.com/mhajder/oxidized-mcp/commit/6468c1f8f5b435bd6b3cad3dcf304611a8fdb55d))


### 🧪 Tests

* raise line coverage to 100% ([dd6a6ea](https://github.com/mhajder/oxidized-mcp/commit/dd6a6ea28f2f3514719db4de70eef4032416ca00))

## v0.2.1 (2026-10-23)

### Fix

- **deps**: update certifi to 2026.10.19

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
