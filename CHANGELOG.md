# Changelog

## [Unreleased]

### Changed
- Load saved-instance connections independently and reuse dashboard snapshots with visible freshness (#23).
- Collect server summary metrics in one SSH command and reuse empty AWG counter snapshots (#23).

### Fixed
- Bound SSH channel execution and reconcile server observations without overwriting concurrent changes (#23).

## [1.10.1] - 2026-11-02

### Fixed
- Run Smart Route own-subnet copies and instance-marker writes inside one sudo shell for non-root SSH accounts (#19).

## [1.10.0] - 2026-11-02

### Added
- AWG kernel and userspace component updates with progress, cancellation, and recovery (#13).
- Published Compose image qualification on native Linux (#10).

### Fixed
- Preserve peer access decisions through component updates and rollback (#13).
- Require authoritative peer state before archiving server accounting ownership (#13).
- Restore kernel modules and managed AWG/Exit tunnels after failed updates (#13).
- Recover lost AWG and Exit interfaces while preserving peer configuration (#13).
- Accept admin API tokens on user and configuration routes (#13).
- Restore RKN block startup and parallel Smart Route status rendering (#13).
- Resume update polling when reopening the server modal (#13).

### Security
- Bind updates to stable server identities and serialize conflicting operations (#13).
- Use private per-operation userspace staging and verify pinned source build identity (#13).

## [1.9.0] - 2026-11-02

### Added
- Verified Linux/Windows binary builds, GHCR digest promotion, and resumable releases (#5).
- Stable update checks from this repository with feedback in all five locales (#5).

### Changed
- Manage Python dependencies through uv and a committed lockfile (#5).
- Install published project GHCR images through Compose, with a separate local WARP override (#5).
- Exclude macOS binaries from distribution (#5).

## [1.9.0-rc.1] - 2026-11-02

### Added
- Verified Linux/Windows binary builds, GHCR digest promotion, and resumable releases (#5).
- Stable update checks from this repository with feedback in all five locales (#5).

### Changed
- Manage Python dependencies through uv and a committed lockfile (#5).
- Install published project GHCR images through Compose, with a separate local WARP override (#5).
- Exclude macOS binaries from distribution (#5).
