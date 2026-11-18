# Branch Guide

This repository is the home repo for the SNR work. This same guide is kept on each local branch.

## Local branches

| Branch | Purpose |
|---|---|
| `main` | Starting point from the `snr_work` project snapshot. |
| `feature/v2-storyline-alerting` | Connected v2 baseline branch; it now includes the v3 updates as well. |
| `feature/v3-alerts-and-zones` | Branch for the v3 story-line, zone, alert, and chart upgrades. It currently points to the same commit as `feature/v2-storyline-alerting`. |
| `snr-v3` | Dedicated branch containing the complete SNR v3 system (storylines, zones, setups, and charts). |
| `snr-v4` | Dedicated branch for SNR v4, adding Setup 4 (15-min trades at H1/H4 levels), M15 candle feeder (v1.2), and 15-min classification. |
| `v2-baseline` | Local copy of the original v2 baseline history. |
| `v3-upgrade` | Local copy of the original v3 upgrade history, stacked on the v2 baseline. |

## Branch relationships

The connected feature line is:

`main` → `feature/v2-storyline-alerting` → `snr-v3` → `snr-v4`

The v2 source branches were imported from the separate `snr_system_v2` repository. The `v2-source/v2-baseline` and `v2-source/v3-upgrade` remote-tracking refs preserve those original source histories.

## Branch Management Rules

1. **Version Isolation**:
   - Each major version branch (`v2-baseline`, `snr-v3`, `snr-v4`) must maintain its specific feature scope.
   - Do not commit higher-version features (e.g. M15 Setup 4 logic) into earlier version branches (`snr-v3`, `main`, `v2-baseline`).

2. **Branch Guide Synchronization (All Changes)**:
   - `BRANCHES.md` is replicated across all branches.
   - Whenever any branch is added, deleted, renamed, or repurposed, `BRANCHES.md` must be updated and committed across **all local branches** to ensure consistent branch layout documentation.

3. **Forward Change Flow**:
   - Common bugfixes, feeder updates, and general infrastructure improvements made on earlier branches should be merged or cherry-picked **forward**:
     `main` → `feature/v2-storyline-alerting` → `snr-v3` → `snr-v4`.
   - Never backport forward-version breaking features to prior version baselines.

4. **Commit & Workflow Discipline**:
   - Branch transitions must always begin with a clean working tree (no uncommitted or unstaged changes).
   - Local environments (`.venv`), database files (`*.db`), generated charts (`setups/`), and archive files (`*.zip`) must remain git-ignored across all branches.
