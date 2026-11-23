# Protocol installation

## Purpose

Let administrators tell whether a protocol installation started and obtain its actual outcome when the panel can confirm it.

## ADDED Requirements

### Requirement: An admitted installation returns its actual result

The install endpoint SHALL keep an admitted installation exclusive through completion and return its terminal result to a connected caller even after the ordinary lifecycle response deadline.

#### Scenario: Slow AmneziaWG 3.1 installation succeeds

- **WHEN** the Docker build outlasts the ordinary response deadline and the installer subsequently completes and saves the requested instance
- **THEN** the original request returns success with the installed protocol key and installation log
- **AND** the installer runs once

#### Scenario: Late Docker failure

- **WHEN** the installer reports a Docker build failure after the ordinary response deadline
- **THEN** the original request returns that failure and the panel does not mark a new instance as installed

### Requirement: Admission failures have distinct outcomes

The panel SHALL distinguish an install request denied by contention from a lifecycle operation fenced because an SSH command has an unknown outcome.

#### Scenario: Another operation holds the lock

- **WHEN** the request exceeds the admission budget while another operation holds the lock
- **THEN** it receives HTTP 503 with code `peer_lifecycle_busy` and a Retry-After header
- **AND** the installer is never invoked for that request and the dialog says it did not start

#### Scenario: SSH outcome requires recovery

- **WHEN** a persisted uncertainty fence prevents admission or an SSH result becomes uncertain during the install
- **THEN** the endpoint returns the recovery-required error `peer_lifecycle_uncertain` and the dialog directs the administrator to recovery

### Requirement: The dialog reports only confirmed progress and outcomes

The installation dialog SHALL indicate an outstanding request, render the installer’s terminal response when available, and identify an unconfirmed outcome when the response is lost.

#### Scenario: Waiting for the panel

- **WHEN** the browser has submitted an install request and has received no progress events or final response
- **THEN** it displays a waiting indication without assigning Docker stages or percentage completion to elapsed time

#### Scenario: A disconnected caller loses the result

- **WHEN** the browser loses the install response during a first install, reinstall, or additional instance install
- **THEN** it tells the administrator to verify the target instance before retrying and never resubmits the install automatically
- **AND** container visibility or a previous installation record alone cannot cause the dialog to announce success
- **AND** an admitted worker retains exclusive access until it finishes even if the caller disconnects
