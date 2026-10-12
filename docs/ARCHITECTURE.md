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
| `src/smart_file_organizer/main.py` | Startup: data folder, logging, settings, database, window. |
| `config.py` | Default category rules, the `Settings` dataclass, JSON load and atomic save. |
| `core/models.py` | Plain dataclasses shared across layers: `FileEntry`, `ScanResult`, `OrganizePlan`, `BatchOutcome`, and history records. |
| `core/scanner.py` | Iterative directory walk with exclusions, cancellation, progress and per-item error capture. |
| `core/classifier.py` | Extension → category → destination folder. Never opens files. |
| `core/organizer.py` | `build_plan` (pure) and `execute_plan` (guarded I/O). |
| `core/fileops.py` | `move_no_overwrite`: the single primitive that moves files. |
| `core/undo.py` | `preview_undo` (read-only checks) and `undo_batch` (all-or-nothing preflight, then guarded moves). |
| `core/history.py` | `HistoryRepository`: batches, operations, scan summaries, statistics, recovery. |
| `core/duplicates.py` | Size grouping, partial and full SHA-256 hashing, change detection. |
| `core/large_files.py` | Filters and sorts a completed scan. |
| `core/metadata.py` | Lightweight metadata and image dimensions from file headers. |
| `core/watcher.py` | `FolderWatcher` (poll, stability, de-duplication, pause/resume/stop) and `WatchManager`. |
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
   3. Pick a final name. Existing files are never overwritten.
   4. Record a `pending` operation row, move with `move_no_overwrite`, then update the row to
      `success` or `failed`. A crash between steps leaves `pending`, which becomes `interrupted`
      on the next start.
5. The batch is finished with `completed`, `partial` or `cancelled`.

### Undo

1. `preview_undo` lists every successful move not yet undone and checks each against the
   filesystem (`_check_move`). Nothing is changed.
2. If any item is blocked, `undo_batch` refuses and returns the reasons.
3. Otherwise each move is re-checked and reversed with `move_no_overwrite`. Empty folders the
   batch created are removed. The batch is marked `undone`, or `undo_partial` if something failed,
   in which case the remaining moves can be retried.

### Folder monitoring

`FolderWatcher.poll_once` rescans the folder, builds a plan, and considers only files whose
size and timestamp have been stable for `stable_seconds` (10 s). Each handled file is remembered
by (path, size, mtime), so it is not processed again unless it changes. Preview mode stores the
stable files as pending. Auto-apply executes them through the same `execute_plan` path, so all
collision, exclusion and revalidation rules apply.

## Threading model

- The Tk main thread owns all widgets. It never blocks on I/O.
- `TaskRunner.submit` runs work on a daemon thread. Progress, results and errors are placed on a
  queue and delivered to the main thread by polling with `after` (about every 40 ms).
- Each thread gets its own SQLite connection (`Database.connection`), so workers and the UI never
  share a connection. Writes use `BEGIN IMMEDIATE`, and WAL mode lets readers proceed during writes.
- Cancellation is cooperative: workers check a `threading.Event` between files.
- Monitoring threads are daemon threads with a stop event; `WatchManager.stop_all` runs on close.

## Data

| Item | Location (Windows) | Notes |
| --- | --- | --- |
| Settings | `%APPDATA%\SmartFileOrganizer\settings.json` | JSON, written atomically. Unknown keys are ignored. |
| History | `%APPDATA%\SmartFileOrganizer\history.sqlite3` | Batches, operations, scan summaries. Never stores file contents. |
| Log | `%APPDATA%\SmartFileOrganizer\app.log` | Rotating, 1 MB × 3. Tracebacks are logged here, never shown to users. |

Override the location with the `SFO_DATA_DIR` environment variable (used by the test-suite).

### Schema (version 1)

- `batches(id, kind, root_name, root_path, dry_run, status, created_at, finished_at, note)`
- `operations(id, batch_id → batches, op_type, source_path, destination_path, size_bytes,
  modified_ns, file_id, status, error, created_at, undo_status, undo_error, undone_at)`
  with indexes on `batch_id`, `status`, and `batches.created_at`.
- `scans(id, root_name, file_count, folder_count, total_bytes, category_counts, issue_count, created_at)`

## Design decisions

- **No third-party dependency beyond CustomTkinter.** Hashing, scanning, SQLite and the watcher
  use the standard library. Polling was chosen over OS notifications for portability.
- **Per-file moves, batch-level undo.** Filesystems cannot commit multiple renames atomically, so
  each move is an independent, recorded step. Undo is all-or-nothing at preflight time.
- **Plans are pure data.** `OrganizePlan` can be inspected, dry-run and serialized without I/O,
  which keeps previews and actual execution identical.
- **Only the application's own database is ever renamed,** and only when it is corrupt.
- **Results on screen are "current" and history is "recorded".** The dashboard keeps them apart.

## Testing

`tests/` mirrors the core modules. Every test uses temporary directories and synthetic data,
and `conftest.py` redirects the data folder so the real profile is never read or written. Symlink
and permission tests skip themselves where the environment does not allow them.
