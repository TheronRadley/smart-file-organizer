# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
aims to follow [Semantic Versioning](https://semver.org/) once it has its first release.

## [Unreleased]

### Safety (audit on 080e9db)
- **Destination vanishing or changing mid-move can no longer lose files.** If the destination
  cannot be verified after linking, the move aborts with the source left in place; if the
  destination folder is swapped during linking, the redirected link is removed and the move is
  refused. Rollback comparisons now use identity plus size/timestamp (inode-reuse safe), and
  the export publication cleanup verifies the file from its own handle before deleting a
  partial report, so a replacement created during copying is never deleted.
- **History and shutdown coordination.** `History.clear()` is refused while a file operation
  runs; closing the window requires every watcher worker to have exited before SQLite is
  closed; undo and Apply Pending honour the UI task cancellation token; undo journals its
  intent before restoring and leaves unresolved outcomes as recoverable `undo_partial` records
  with identity-verified Recovery guidance; only the primary app instance (data-folder lock)
  runs startup recovery.
- **Automation and preview fixes.** Start restarts fully stopped watchers; automation setting
  changes (including disabling automatic moves) apply to existing watchers; a folder whose
  watcher is still stopping stays listed; Organize preserves file selection across dry runs
  and refreshes, no longer auto-reselecting everything, and changing organization rules
  invalidates the stale preview. History exports are no longer capped at 200 batches.

### Safety (2026-10-28 audit)
- **Source-replacement races closed in `move_no_overwrite`.** The linked destination is verified
  against the validated file (identity, size, and timestamp, which also catches inode reuse), and
  the source path is re-verified immediately before its name is removed, so a file replaced at
  any stage is never the file that gets unlinked or moved. A failed rollback cleanup is now
  always reported with both paths and recovery instructions, never silently suppressed.
- **Destination-folder swap fails closed.** The organizer re-checks destination containment
  after journaling and immediately before each move, so a folder swapped for a symlink or
  junction at any earlier point cannot be written through.
- **Shutdown is safe.** Closing the window cancels background tasks and waits for workers to
  finish before the history database is released; if a worker cannot finish in time, the window
  stays open. A database failure after a successful move leaves the operation pending, so the
  completed move is reconciled and recoverable on the next start instead of vanishing.
- **Automation approvals are identity-bound.** Pending moves now record size, modification time,
  and file identity; "Apply Pending" refuses a same-name replacement even with identical size
  and timestamps. Watcher errors survive until a genuinely successful poll, folder identity is
  normalized (one watcher per folder however the path is spelled), a watcher that is still
  stopping is not discarded, and manual, undo, and automation operations share a per-folder
  lock so they cannot modify the same tree concurrently.
- **Partial exports are cleaned up.** If the no-overwrite publication copy fails halfway, the
  incomplete destination is removed and the failure is reported. NaN and infinity are rejected
  in numeric settings and UI inputs, and duplicate-scan cancellation is now checked between
  hashing chunks, not just between files.

### Safety (2026-10-26 audit and roadmap)
- **Protected locations are refused as scan roots.** Drive/filesystem roots and operating-system
  folders (such as `C:\Windows`, `C:\Program Files`, `/etc`, `/usr`) can no longer be organized,
  watched, or duplicate-scanned; the refusal applies through the single scan entry point shared
  by every feature. Ordinary user folders are unaffected.
- **Adversarial integration test suite** (`tests/test_adversarial_safety.py`): destination folder
  swapped for an escaping symlink after preview, competing process creating the planned
  destination, simulated cross-volume failure, hard-linked names, seeded property-style
  collision-name checks, and protected-root checks. Expected outcome everywhere: no overwrite,
  no deletion, no escape from the selected root.
- CI now also runs the test matrix on macOS (in addition to Windows and Linux).

### Security (2026-10-26 audit)
- **CSV exports can no longer smuggle spreadsheet formulas.** Cells whose first
  non-whitespace/control character is `=`, `+`, `-` or `@` (including tab/newline/BOM-prefixed
  variants) are apostrophe-prefixed so Excel/LibreOffice open them as text; JSON is unchanged.
- **Exports never silently replace an existing report.** `write_export` refuses an existing
  target unless `overwrite=True` is passed (the native save dialog is that confirmation in the
  UI), writes to a uniquely named temporary file (no shared `.tmp` collisions between concurrent
  exports), and publishes without clobbering a target created during the write.
- **Watcher "Apply Pending" now moves only the exact mapping that was approved.** If a rebuilt
  plan would send a file to a different destination than the one displayed, that file is not
  moved; its new destination is shown for renewed approval.

### Added
- Desktop interface (CustomTkinter) with Dashboard, Organize Files, Duplicate Finder,
  Large Files, Automation, History, Settings and About pages.
- Extension-based classification into ten default categories plus `Other`, with
  configurable destination folder names, extension overrides and disabled categories.
- Recursive or top-level scanning with exclusions, cancellation, progress reporting,
  and graceful handling of unreadable items. Symbolic links and junctions are never followed.
- Organization planning by file type, extension or modified month, with a complete preview,
  dry-run mode, per-file selection and collision handling (safe renaming or skipping).
- Guarded execution: every operation is re-validated immediately before it runs, moves never
  overwrite existing files, and each outcome (success, skipped, failed) is recorded.
- SQLite history of batches and operations with versioned migrations, crash recovery
  (interrupted work is marked on next start), corrupt-database preservation and a
  clear-history action that never touches user files.
- Guarded undo: a batch is reversed only when every file is still where it was organized and
  its original location is free; otherwise nothing is changed and the reasons are shown.
- Duplicate detection by size, partial hash and full SHA-256, excluding symbolic links and
  files that change while being read. Hard links are not counted as recoverable space.
- Large-file listing with minimum size and extension filters, and an incomplete-results warning.
- File metadata preview, including image dimensions read from file headers.
- Optional folder monitoring that is preview-first, with pause, resume and stop, persistent
  settings, and explicit opt-in for automatic organization.
- Reports: export history, scan results and duplicate reports to CSV (UTF-8 with BOM) or JSON.
- Recovery guidance for batches that did not finish cleanly, naming where each file is now and
  the safest next step, shown on the History page and offered automatically for interrupted,
  partial and cancelled batches.
- CI: `ruff format --check`, a `pip-audit` dependency audit, a Windows GUI startup smoke test,
  and Dependabot for dependency and action updates.
- Persistent settings in the per-user data folder, with light and dark themes.
- pytest test suite, Ruff configuration, GitHub Actions workflows for tests and a Windows build.
- Documentation: README, user guide, safety guide, architecture notes, contributing guide.

### Fixed
- **Stop now stops automation.** A stop request is propagated into the scan and the move batch,
  so a poll in progress can no longer move files after Stop was pressed; the worker thread handle
  is kept until the thread has really exited, so a second Start can no longer run two watchers for
  one folder; files a cancelled batch never reached are still organized after a later start.
- **A failed move can no longer delete a file it did not create.** Rollback removes the extra hard
  link only after proving by device and inode that it is still the link this operation created and
  still refers to the same file object; a destination another program replaced in the meantime is
  kept and reported with both paths instead of being unlinked.
- **Check/use races closed.** Files are hashed and read for image dimensions through a handle
  opened without following links and compared with the scanned identity; sources are re-validated
  after the destination folder is created and the move refuses a source that is no longer a
  regular file or no longer carries the recorded identity; undo re-checks after recreating the
  original folder.
- **Settings contents are validated, not just their container types.** Invalid items inside
  `excluded_folders`, `excluded_patterns`, `watch_folders`, `disabled_categories`,
  `category_folders` and `extension_overrides` are dropped (or fall back to the defaults) instead
  of crashing a later scan or the folder configuration.
- Resolved Windows file identity detection (`file_identity`) when `os.DirEntry.stat()` reports
  `st_ino == 0` by querying `os.lstat()` and Win32 handle information so hard-link deduplication,
  source replacement checks and guarded undo work reliably on Windows.
- Closed leaked SQLite connections when `Database` initialization, migration or integrity checks
  fail, preventing `PermissionError` (`WinError 32`) during corrupt-database recovery on Windows
  and preserving corrupt databases (and `-wal`/`-shm` companions) under unique backup names.
- Removed the unsafe POSIX check-then-rename fallback in `move_no_overwrite`, failing closed
  with a clear error when hard links are unavailable on POSIX so existing destination files can
  never be overwritten concurrently.
