# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
aims to follow [Semantic Versioning](https://semver.org/) once it has its first release.

## [Unreleased]

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
