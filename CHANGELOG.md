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
- Persistent settings in the per-user data folder, with light and dark themes.
- pytest test suite, Ruff configuration, GitHub Actions workflows for tests and a Windows build.
- Documentation: README, user guide, safety guide, architecture notes, contributing guide.

### Fixed
- Resolved Windows file identity detection (`file_identity`) when `os.DirEntry.stat()` reports
  `st_ino == 0` by querying `os.lstat()` and Win32 handle information so hard-link deduplication,
  source replacement checks and guarded undo work reliably on Windows.
- Closed leaked SQLite connections when `Database` initialization, migration or integrity checks
  fail, preventing `PermissionError` (`WinError 32`) during corrupt-database recovery on Windows
  and preserving corrupt databases (and `-wal`/`-shm` companions) under unique backup names.
- Removed the unsafe POSIX check-then-rename fallback in `move_no_overwrite`, failing closed
  with a clear error when hard links are unavailable on POSIX so existing destination files can
  never be overwritten concurrently.
