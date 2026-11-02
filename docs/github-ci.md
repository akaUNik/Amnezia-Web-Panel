# GitHub builds and distribution

The distribution repository is `akaUNik/Amnezia-Web-Panel`.

## Events and verification

| Event | Verification | Binaries | Containers | Publication |
| --- | --- | --- | --- | --- |
| Pull request | `test.yml` | — | — | None |
| Push to main | Called `test.yml` | Both | Both variants | GHCR |
| Push of `v*` tag | Called `test.yml`, version identity | Both | Both variants | GHCR and Releases |
| Manual build, any ref | Called `test.yml`, source version | Both | Build only | Actions artifacts only |
| Manual tests | `test.yml` | — | — | None |

`build.yml` calls the local reusable workflow through `tests`. `version`
reads the literal `CURRENT_VERSION` without importing the application. Both
`build` and `docker` need `tests` and `version`. `prepare` waits for all four,
validates the two binary sets and both container digests, then saves exactly
four prepared release assets. `promote` waits for `prepare`, verifies registry
identities, and advances container tags by digest. `release` waits for all
these jobs, including `promote`, and runs only for version pushes.
Every checkout uses the event SHA, including the synthetic merge SHA for PRs.
There is no independent push trigger in `test.yml`.

Verification retains unittest discovery, Chromium, and the root runs of
`test_panel_backup.py`, `test_rknblock_app.py`, `test_server_page_render.py`,
and `test_destructive_accounting_guards.py`, using temporary state.

Only push events in the distribution repository on main or `v*` can log in
and push. Only version pushes can create a release. Manual builds and forks
do not publish. Default permissions are `contents: read`; container jobs
alone request `packages: write`, and release publication requests
`contents: write`. Binary jobs also request `actions: read` solely to download
their just-uploaded artifacts with GitHub CLI. Each native runner validates
readback SHA/version/architecture/checksum and repeats HTTP/shutdown smoke
checks on the downloaded executable.

Build workflows share a repository concurrency group with
`cancel-in-progress: false`. An active publication is never cancelled by a
new push. GitHub may replace an older pending run in the concurrency group;
rerun a cancelled version build if it is still needed. A main run checks the
current main SHA before promoting main/latest aliases.

## Version and dependencies

Before a release, update `CURRENT_VERSION` in `app.py` and the project version
in `pyproject.toml`, move Unreleased entries to the matching version section
in `CHANGELOG.md`, then run `uv lock` and commit `uv.lock`. Release notes use
that version's changelog sections and a comparison link when available.
Push the release
tag only after the normal Gitflow release PR process. The tag must be valid
SemVer and match the application version, ignoring the optional `v` prefix.
A mismatch or malformed tag stops the build graph before GHCR push. Release
tags must omit build metadata (`+build.7`): Docker does not permit `+` in tags.
SemVer comparison in the application still accepts and ignores build metadata.

Dependencies live in `pyproject.toml` and `uv.lock`. CI uses uv 0.10.5 and
Python 3.11; Docker retains Python 3.14 and explicitly selects the base image
interpreter. `uv sync --locked` installs test dependencies;
`uv sync --locked --no-dev --group build` installs PyInstaller;
`uv sync --locked --no-dev` installs runtime dependencies only. An outdated
lockfile fails CI. Local `.venv` must never enter the Docker build context.

## Binary downloads

| Artifact | File | Runner / architecture |
| --- | --- | --- |
| `binaries-Linux` | `Amnezia-Web-Panel-Linux` | ubuntu-24.04 / x86_64 |
| `binaries-Windows` | `Amnezia-Web-Panel-Windows.exe` | windows-2022 / x86_64 |

Each artifact also contains its uniquely named `metadata-<OS>.json` with
commit, version, architecture, filename, and SHA256. Both sets are retained
for 30 days. macOS executables are excluded from distribution.

Choose a successful build in Actions and download the two named artifacts,
or use a GitHub-authenticated CLI (Actions downloads require sign-in):

```sh
gh run download RUN_ID --repo akaUNik/Amnezia-Web-Panel --name binaries-Linux --dir linux
gh run download RUN_ID --repo akaUNik/Amnezia-Web-Panel --name binaries-Windows --dir windows
chmod +x linux/Amnezia-Web-Panel-Linux
./linux/Amnezia-Web-Panel-Linux
```

On Windows, run `.\windows\Amnezia-Web-Panel-Windows.exe` in PowerShell.
The panel listens on localhost port 5000 by default. Choose an explicit
`DATA_FILE` path to keep state across upgrades. Remote download and smoke
qualification remains required before these commands are considered verified
against a delivered workflow run.

## Repository permissions and storage

Enable GitHub Actions and allow the pinned GitHub, Astral, and Docker actions
used by these workflows. GitHub-hosted runners must support Node 24 actions
(runner >= 2.327.1; checkout's container-auth use requires >= 2.329.0).
Actions must be able to obtain the job-specific `GITHUB_TOKEN` permissions
above. No personal access token is needed in the workflow or application.

Set the repository/organization artifact retention limit to at least 30 days.
Workflow binary artifacts use 30 days; release assets are independent of
Actions retention. Verify Actions storage quotas before relying on downloads.

Action revisions were checked against their official repositories on
2026-11-02: [checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python),
[setup-uv](https://github.com/astral-sh/setup-uv),
[upload-artifact](https://github.com/actions/upload-artifact),
[download-artifact](https://github.com/actions/download-artifact),
[Docker actions](https://github.com/docker/build-push-action).
Workflows pin full commit hashes; update a pin only after reviewing the
corresponding action's runtime and runner requirements.

## GHCR package and channels

The package is `ghcr.io/akaunik/amnezia-panel`. Images carry
`org.opencontainers.image.source=https://github.com/akaUNik/Amnezia-Web-Panel`,
`org.opencontainers.image.revision=<full SHA>`, and the source version.
The default variant supports linux/amd64 and linux/arm64; WARP supports
linux/amd64. On the first push, confirm package repository association,
Actions access/inherited permissions, and **public** visibility in the
owner's package settings. A source label alone does not make a package public.

Docker builds push only unique `staging-<SHA>-<run>-<attempt>-<variant>` tags.
Manual runs do not log in or push. `container-default` and `container-warp`
artifacts store digest identities separately; build record uploads are disabled.
Neither container records nor `.dockerbuild` files become release assets.

| Source | Promoted tags (each also has a `-warp` counterpart) |
| --- | --- |
| Current main | `main`, `sha-<full SHA>`, `latest` |
| Stale main | `sha-<full SHA>` only |
| `v1.9.0` | `1.9.0`, `1.9`, `1`, `latest` |
| `v1.10.0-rc.1` | `1.10.0-rc.1` only |

Exact version/SHA tags reject conflicting digests. Stable major/minor/latest
aliases are preserved if they already represent a higher version, or an equal
version from another SHA. Prereleases never change stable aliases or GitHub's
stable latest release. Both variants and all binaries must succeed before
promotion starts. Registry and GitHub Releases are separate services; network
failure during promotion can still leave a partially updated pair.

Inspect published manifests and confirm anonymous pulls (WARP is amd64 only):

```sh
docker buildx imagetools inspect ghcr.io/akaunik/amnezia-panel:latest
docker buildx imagetools inspect ghcr.io/akaunik/amnezia-panel:latest-warp
anon_config=$(mktemp -d)
docker --config "$anon_config" pull ghcr.io/akaunik/amnezia-panel:latest
docker --config "$anon_config" pull --platform linux/amd64 ghcr.io/akaunik/amnezia-panel:latest-warp
```

Before any alias write, the promotion summary records previous digests and
then records each starting/completed update. Save that summary when an attempt
fails. Rerun failed jobs with original artifacts to finish promotion, or restore
an affected alias to its recorded digest (repeat for the WARP counterpart):

```sh
docker buildx imagetools create --prefer-index=false --tag ghcr.io/akaunik/amnezia-panel:latest ghcr.io/akaunik/amnezia-panel@sha256:PREVIOUS_DIGEST
```

## Complete releases and retries

A version release has exactly four assets: `Amnezia-Web-Panel-Linux`,
`Amnezia-Web-Panel-Windows.exe`, `build-info.json`, and `SHA256SUMS`.
The manifest includes both image digests, source SHA/version, binary OS/arch,
source ref, and run URL. `SHA256SUMS` covers both binaries and build-info,
excluding itself. Releases persist independently of Actions retention.

Publication resolves lightweight/annotated tags to the exact build commit,
creates an owned draft, uploads the verified set, downloads every asset back,
and checks identities/checksums before publishing. A failed upload stays draft.
Prereleases set `prerelease=true` and `make_latest=false`; an older stable
release does not replace a newer latest release.

```sh
gh release download v1.9.0 --repo akaUNik/Amnezia-Web-Panel --dir release-v1.9.0
cd release-v1.9.0
sha256sum -c SHA256SUMS
chmod +x Amnezia-Web-Panel-Linux
./Amnezia-Web-Panel-Linux
```

On Windows PowerShell, use `Get-FileHash -Algorithm SHA256 FILE` for each
file and compare with `SHA256SUMS`, then run the executable. Download all four
files together when verifying a release. These commands are examples for an
agreed future version, not evidence that v1.9.0 has been published.

Use **Re-run failed jobs**, retaining the successful jobs' original artifacts.
The helper resumes only its own draft for the same tag/SHA. A complete,
matching published set is a no-op; a published SHA/checksum/content conflict
is an error and is never overwritten. Rebuilding a published version can
produce different bytes despite the same source SHA; do not replace published
assets with that rebuild. Restore original artifacts or publish a new version.
Unrelated drafts, mixed-SHA metadata, missing digests, or extra assets require
operator investigation. Do not delete persistent data volumes during recovery.

## Local validation evidence

On 2026-11-02, uv lock/sync and pin preservation checks passed. The Linux
arm64 runtime image built using Python 3.14.10, served login/static HTTP,
and shut down cleanly. Runtime contains neither Playwright nor PyInstaller.
All 112 isolated root regression tests passed without skips.

The macOS full run (1253 tests before adding workflow contracts) reproduced
the same 2 failures/4 errors/1 skip as unchanged develop (1249 tests). Physical
temporary paths avoid macOS symlink rejections. All five clients-table tests
pass on Linux. An old SSH assertion also failed on unchanged Linux develop;
with separate user approval it now checks `SSHConnectionUnavailable`, its
original `OSError` cause, retries, and cooldown. That corrected test passes
on Linux. These checks do not replace full local or remote qualification.


Settings UI qualification uses Python Playwright because the Browser plugin
is not available. At `http://panel.test/settings`, Chromium verified all five
locales, new/no-release/error/retry states, safe version rendering, permanent
own-Releases links, and restored controls. Desktop 1280x900 and mobile 375x900
were checked; labels wrap on mobile. API requests were intercepted and there
were no page errors or requests to upstream release endpoints. Screenshots
are local QA artifacts, not committed distribution files.

## Completed qualification, 2026-11-02

The implementation commit tested by the final manual run is
`eebeefa191b860520034fe18eab26e0a8d60890c`.

- [Manual run 41434416280](https://github.com/akaUNik/Amnezia-Web-Panel/actions/runs/41434416280): verification, both native binaries, default amd64/arm64 Docker, and WARP amd64 Docker all passed. Each native job used the documented `gh run download`, verified metadata and SHA256, then successfully repeated login/static/shutdown smoke on the downloaded file.
- [PR verification 41434072391](https://github.com/akaUNik/Amnezia-Web-Panel/actions/runs/41434072391): full Linux/Python 3.11 unittest and Chromium coverage passed, followed by all 112 isolated root regressions.
- Full local Python 3.11/Chromium run: **1286 tests, 0 failures, 0 errors**. The one skipped import is the pre-existing pytest-only live-server module, outside required unittest coverage. No required group was skipped. The host was macOS; only synthetic clients-table SSH shell scripts were executed in isolated Linux containers to preserve the target server's GNU chmod semantics. Original assertions and other shell fixtures were retained. This is a local hybrid run, not a claim of a full native Linux run.
- Final local Linux arm64/Python 3.14 runtime image: all **112 root regressions** and six focused clients-table/SSH tests passed without skips. Login/static HTTP smoke, clean shutdown, resource presence, ignored local directories, and runtime-only dependency groups were verified.
- `actionlint`, translation completeness, Node syntax/SemVer/API doubles, release/promotion doubles, lock consistency, and all three Compose configurations passed. `sha256sum -c SHA256SUMS` passed against a complete synthetic four-asset set; release download options were checked against GitHub CLI help. Real version-release qualification remains pending.

| Native artifact | Artifact ID | ZIP bytes | Expires (UTC) |
| --- | --- | --- | --- |
| [binaries-Linux](https://github.com/akaUNik/Amnezia-Web-Panel/actions/runs/41434416280/artifacts/13135957399) | 13135957399 | 44308376 | 2026-12-02 11:13:01 |
| [binaries-Windows](https://github.com/akaUNik/Amnezia-Web-Panel/actions/runs/41434416280/artifacts/13135932378) | 13135932378 | 30244738 | 2026-12-02 11:12:57 |

Readback binary SHA256 (ZIP checksums are different):

```text
f833e9be300c414bffd83fdbc1b6af37ceb99d65d7d0db343518b29b9b4e90b2  Amnezia-Web-Panel-Linux
d32db466249a2174247bafb5c7bfe60fedab17f11e7cae339b006e9630c59d95  Amnezia-Web-Panel-Windows.exe
```

The final manual run produced exactly those two artifacts. `prepare`,
`promote`, and `release` were skipped. The Releases API returned an empty list
both before and after manual qualification; no draft or published release was
created. The GHCR package endpoint returned 404 both before and after with the
available credentials. Public package visibility, anonymous pulls, and a real
main pipeline are therefore still **unverified**, tracked in task 7.3; the
manual run's login/push guards and skipped publishing jobs confirm it did not
promote production tags.

Actions settings were checked: enabled, all actions allowed, default token
permissions read-only. Artifact API timestamps confirm the configured 30-day
retention. Real prerelease/stable publication, complete release readback,
latest-channel checks, and a live stable update lookup remain task 7.4 and
require an agreed release version. No synthetic stable release was created.

| Settings UI check | Result |
| --- | --- |
| Page identity and meaningful content | Pass: /settings, localized title/content |
| Framework error overlay / app page errors | Pass: none |
| New release → 404 → error → retry | Pass in en/ru/fr/zh/fa |
| Trusted Releases link and safe version text | Pass |
| Screenshots and label layout | Pass at 1280x900 and 375x900 |

Local QA evidence is stored outside the repository in
`/private/tmp/amnezia-updates-qa/`; full local and native job logs are in
`/private/tmp/amnezia-local-full-tests-verified.log`,
`/private/tmp/amnezia-final-root.log`, and
`/private/tmp/amnezia-native-{linux,windows}.log`.
