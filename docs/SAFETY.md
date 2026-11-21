# Safety

Smart File Organizer moves real files. Start with a small test folder, review the preview, and
keep backups of files you cannot replace. Undo reverses moves; it is not a backup.

## What the app does

- **Preview and approval.** Manual organization and **Apply waiting files** in Automation move
  only the approved files to the approved destinations. The app does not substitute a later
  watcher list. If a file changed or a destination became occupied, rebuild or review a fresh
  preview. The collision rule is used when making the preview, not to silently choose a new name
  during a manual apply. Automatic organization is the explicit opt-in exception to previewing.
- **Dry runs.** A dry run checks a preview without creating folders, moving files, or writing
  history. It cannot prove that the filesystem will permit the actual rename later.
- **One atomic, no-replace move per file.** The app uses the platform's non-replacing rename
  (Windows, Linux, and macOS where supported). If the filesystem doesn't support it, the app
  refuses the move. It does not copy/delete across volumes or fall back to linking and unlinking
  the source, so there is no move rollback that deletes a second name.
- **Source and destination checks.** Immediately before a move or restore, the app checks the
  source's file type, identity (when available), size, and modification time. It checks that both
  paths stay in the approved tree, refuses link-like directories, and checks the root's recorded
  identity for new batches. POSIX moves address the entries through open directory descriptors.
- **Protected locations.** Filesystem roots and known OS folders such as `C:\Windows` and `/usr`
  aren't valid scan roots. The active app data directory (`SFO_DATA_DIR` or the default) is also
  refused as a root, excluded from recursive scans, and protected again at apply and undo time.
  Scans skip detected symlinks, junctions and other link-like entries rather than descending into
  them. Hidden and temporary names are excluded by default; those name rules are configurable.
- **History and guarded undo.** Before moving a file, the app records the intended source and
  destination. Undo checks all recorded outstanding moves before starting. Original locations
  must be free, and moved files must still match their recorded metadata. Undo only removes an
  empty folder if it can verify that this batch created that directory. Old history may lack
  directory identities; those folders are left in place.
- **Export consent.** A new report is published without overwriting a file that appears at the
  chosen name, using the same native no-replace rename. To replace an existing file, confirm
  that file; it is checked again before publication. Report paths inside the active profile
  are refused. Duplicates and large-file searches never delete files.
- **One writer per data folder.** A second instance using an in-use data folder exits without
  opening its history. Within the app, overlapping file operations cannot run together, and
  **Clear history** is excluded for the entire time a move or undo batch is active (and vice versa).
- **Local and unprivileged.** No network access, telemetry, cloud services, or administrator rights
  are needed. Classification uses the filename alone. Exports and logs may contain local paths;
  review them before sharing.

## Limits to keep in mind

- **A batch is not atomic.** Some files may move before a later file fails, the batch is cancelled,
  or the process stops. Undo is all-or-nothing only at preflight; changes or cancellation after it
  starts can leave a partial restore. Edited, missing, or replaced files cannot be restored by Undo.
- **Filesystem support varies.** The rename must be on a single volume with support for a
  no-replace rename and verifiable directory identity. Some network or removable volumes may
  refuse these operations. The source is not removed if the rename is refused. Device/inode,
  size and timestamps aren't content hashes; an identical recorded signature cannot distinguish
  two versions. On filesystems without a file ID, verification is more limited.
- **Other programs are not locked out.** The app rechecks paths and file metadata, but those checks
  are not a transaction with another program editing or replacing a file or directory. The native
  rename prevents clobbering a destination; it doesn't prevent other programs from changing files
  before or after the rename. On Windows the path checks and rename are separate operations. For
  an explicitly approved export replacement, the final check and replacement are also separate.
  Avoid organizing folders that other programs are actively rearranging.
- **Monitoring uses polling, not copy-completion signals.** A stable size and timestamp are a
  heuristic. Be careful with downloads or copies that pause for long periods. Changes to watcher
  rules invalidate existing approvals. Stop and Close signal every watcher without waiting on
  the Tk thread; an operation already in progress may finish, but no further files are started
  once the worker observes cancellation. The window waits for workers before releasing the database.

## If something goes wrong

**Partial or interrupted batch.** In *History*, open the batch and read *Error / reason*. Use
**Recovery guidance…** for operations that need attention, including ones whose batch was
previously labeled completed. A move or restore can succeed before its final history write;
**Recovery guidance…** uses the journal to compare the recorded file to both paths:

- **Verified:** a regular file matches the recorded identity and available size and timestamp.
  If an unrecorded forward move is verified at the destination, Undo cannot reverse that move.
- **Missing:** the recorded path couldn't be found.
- **Unverified:** an entry may be present but may not be the right file, or it could not be checked.
  Inspect both paths before retrying or removing anything. Two entries can be leftover hard links
  from a batch run by an older version. The app does not delete either for you.

**Undo refused or only partly completed.** Resolve the reported conflict before retrying. A file
with an unrecorded restore may already be back at its original location, so inspect Recovery
before retrying it. History can’t recover edited or deleted contents.

**Damaged history database.** The database is preserved as `history.corrupt-<timestamp>.sqlite3`
and a new history is started. Undo isn't available for batches recorded only in the damaged copy.

**Stop watching a folder.** Press **Stop** or remove the folder from Automation. While a worker is
finishing, its status shows *Stopping*; Start won't launch a second worker. Closing the app also
stops monitoring. If a different process is holding a file open, allow that operation time to finish.

## Reporting a safety problem

If you find a way for the app to unexpectedly move, overwrite, or delete files, use GitHub's
private security advisory feature rather than a public issue. Remove personal paths from any logs
or reports you share.
