# Changelog

## [Unreleased]

## [1.9.0-rc.1] - 2026-11-02

### Added
- Verified Linux/Windows binary builds, GHCR digest promotion, and resumable releases (#5).
- Stable update checks from this repository with feedback in all five locales (#5).

### Changed
- Manage Python dependencies through uv and a committed lockfile (#5).
- Install published project GHCR images through Compose, with a separate local WARP override (#5).
- Exclude macOS binaries from distribution (#5).
