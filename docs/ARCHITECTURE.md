# Architecture

Smart File Organizer is layered so that everything that decides *what* happens lives in
`core`, and everything that shows it lives in `ui`. The core can be tested without a display,
and the interface never reimplements a rule.

```
┌──────────────────────────────────────────────────────────────┐
│ ui/   CustomTkinter window, pages, reusable components        │  presentation only
└───────────────┬──────────────────────────────────────────────┘
                │ calls (never the reverse)
┌───────────────▼──────────────────────────────────────────────┐
│ core/ scanner · classifier · organizer · fileops · undo       │  business logic,
│       duplicates · large_files · watcher · history · metadata │  no tkinter imports
└───────┬──────────────────────────────┬───────────────────────┘
        │                              │
┌───────▼─────────────┐   ┌────────────▼──────────────┐   ┌──────────────────────┐
│ database/           │   │ config.py                 │   │ utils/               │
│ SQLite, migrations  │   │ categories, settings JSON │   │ paths, formatting,   │
│                     │   │                           │   │ OS integration       │
└─────────────────────┘   └───────────────────────────┘   └──────────────────────┘
```

## Packages

| Path | Responsibility |
| --- | --- |
| `app.py` (root) | Source-checkout launcher; adds `src/` to the path and calls `main()`. |
| `src/smart_file_organizer/main.py` | Startup: exclusive data-folder lease, settings, journal recovery, and window. |
| `config.py` | Default category rules, the `Settings` dataclass, JSON load and atomic save. |
| `core/models.py` | Plain dataclasses shared across layers: `FileEntry`, `ScanResult`, `OrganizePlan`, `BatchOutcome`, and history records. |
| `core/scanner.py` | Iterative directory walk with exclusions, cancellation, progress and per-item error capture. |
| `core/classifier.py` | Extension → category → destination folder. Never opens files. |
| `core/organizer.py` | `build_plan` (read-only) and `execute_plan` (guarded mutation). |
| `core/fileops.py` | `move_no_overwrite`: native atomic no-replace rename; `open_read_no_follow`: guarded read handle. |
| `core/boundary.py` | Approved root and directory identities, private data exclusion, and descriptor-relative POSIX mutations. |
| `core/operation_guard.py` | Exclusion for overlapping file operations and history clearing. |
| `core/undo.py` | `preview_undo` (read-only checks) and `undo_batch` (all-or-nothing preflight, then guarded moves). |
| `core/history.py` | `HistoryRepository`: batches, operations, scan summaries, statistics, recovery. |
| `core/duplicates.py` | Size grouping, partial and full SHA-256 hashing, change detection. |
| `core/large_files.py` | Filters and sorts a completed scan. |
| `core/metadata.py` | Lightweight metadata and image dimensions from file headers. |
| `core/watcher.py` | `FolderWatcher` (poll, stability, de-duplication, pause/resume/stop) and `WatchManager`. Stopping cancels the scan and the move batch, and the thread handle is kept until the worker has exited. |
| `core/export.py` | Read-only CSV/JSON exports of history, scan results and duplicate reports. |
| `core/recovery.py` | Guidance for moves that did not finish cleanly: where the file is now, and the safest next step. |
| `database/db.py` | Per-thread connections, `BEGIN IMMEDIATE` transactions, integrity check, corrupt-file preservation. |
| `database/migrations.py` | Ordered schema migrations tracked with `PRAGMA user_version`. |
| `utils/paths.py` | Containment checks, safe folder names, unique names, app data location. |
| `utils/formatting.py` | Human-readable sizes and timestamps. |
| `utils/system.py` | Opening folders in the native file manager without a shell. |
| `ui/app_window.py` | Window, navigation, the shared scan action and page lifecycle. |
| `ui/components/` | `TaskRunner` (background work), `FileTable`, `ScanBar`, `StatCard`, `BarChart`, dialogs, sidebar. |
| `ui/pages/` | One module per navigation page. |

## Key flows

### Organize (scan → preview → dry run → apply)

1. `scan_folder` returns a `ScanResult` (files, folder count, skipped links, issues).
2. `build_plan` maps each file to a destination folder and reserves unique names. Files inside
   managed folders, disabled categories and files already in place are marked `skipped` with a reason.
3. The user selects operations. `execute_plan(dry_run=True)` runs `check_source` on each and
   reports what would happen.
4. `execute_plan(dry_run=False)` for each selected operation:
   1. `check_source`: identity, size and timestamp must match the preview; the source must be a
      regular file inside the root.
   2. Ensure the destination folder is a plain folder inside the root, creating it if needed
      (recorded as a `create_folder` operation).
   3. Keep the exact approved destination. A late collision skips the move rather than replacing
      the target or picking a different name; only opt-in automatic mode can replan a name.
   4. Record a `pending` operation row, then recheck the root, directories and full file signature
      in `move_no_overwrite`. Update the row with the outcome. A crash between steps leaves it
      `pending`, which becomes `interrupted` on the next start; History surfaces it either way.
5. The batch ends as `completed`, `partial`, `cancelled` or `interrupted` when an outcome could
   not be recorded. History uses operation rows as well as batch status to discover unresolved work.

### Undo

1. `preview_undo` lists every successful move not yet undone and checks each against the
   filesystem (`_check_move`). Nothing is changed.
2. If any item is blocked, `undo_batch` refuses and returns the reasons.
3. Otherwise each restore is journaled as `undoing` and guarded again by `move_no_overwrite`.
   A recorded folder is only removed if it is still the same directory and empty. A partial or
   unresolved restore remains visible for retry or recovery.

### Folder monitoring

`FolderWatcher.poll_once` uses one ruleset for classification, exclusions, and planning. It
considers files stable by path, size, mtime and file ID. The Automation page renders an immutable
pending snapshot and passes that exact snapshot to the worker on approval. Rule changes cancel
work using the old rules and invalidate its approvals. Auto-apply is a separate explicit opt-in.

## Threading model

- The Tk main thread owns all widgets. `TaskRunner` queues progress and outcomes from workers
  and delivers them via `after`.
- SQLite connections are per-thread. Task workers and watcher workers release their own
  connections at teardown. Writes use `BEGIN IMMEDIATE`, readers can proceed in WAL mode.
- Close signals task cancellation and all watchers before doing any waiting. An `after` callback
  polls for worker exit before the database and instance lock are released. Stop broadcasts to
  all watchers and returns immediately; the page updates their status as they exit.

## Data

| Item | Location (Windows) | Notes |
| --- | --- | --- |
| Settings | `%APPDATA%\SmartFileOrganizer\settings.json` | JSON, written atomically. Unknown keys are ignored. |
| History | `%APPDATA%\SmartFileOrganizer\history.sqlite3` | Batches, operations, scan summaries. Never stores file contents. |
| Log | `%APPDATA%\SmartFileOrganizer\app.log` | Rotating, 1 MB × 3. Tracebacks are logged here, never shown to users. |

Override the location with the `SFO_DATA_DIR` environment variable (used by the test-suite).

### Schema (version 2)

- `batches(id, kind, root_name, root_path, dry_run, status, created_at, finished_at, note, root_identity)`
- `operations(id, batch_id → batches, op_type, source_path, destination_path, size_bytes,
  modified_ns, file_id, status, error, created_at, undo_status, undo_error, undone_at)`
  with indexes on `batch_id`, `status`, `undo_status`, and `batches.created_at`. For folder creation
  rows, `file_id` records the directory identity used by Undo cleanup.
- `scans(id, root_name, file_count, folder_count, total_bytes, category_counts, issue_count, created_at)`

## Design decisions

- **No third-party dependency beyond CustomTkinter.** Hashing, scanning, SQLite and the watcher
  use the standard library. Polling was chosen over OS notifications for portability.
- **Per-file moves, batch-level undo.** Filesystems cannot commit multiple renames atomically, so
  each move is an independent, recorded step. Undo is all-or-nothing at preflight time.
- **Plans are snapshots.** `OrganizePlan` records proposed paths and their metadata. Dry run and
  apply both recheck the current filesystem; apply also enforces native no-clobber publication.
- **Database corruption handling** preserves only the app’s history database and its companion files.
  It never tries to recreate the contents of user files.
- **Results on screen are "current" and history is "recorded".** The dashboard keeps them apart.

## Testing

`tests/` mirrors the core modules. Every test uses temporary directories and synthetic data,
and `conftest.py` redirects the data folder so the real profile is never read or written. Symlink
and permission tests skip themselves where the environment does not allow them.

Windows CI also launches the **packaged executable**, navigates its pages, and runs a preview,
apply and undo against a synthetic file and an isolated data directory. The build and smoke
scripts in `scripts/` are also the local developer entry points.
