# Safety

Smart File Organizer moves real files, so every design decision favours not losing or
overwriting data over convenience. This document states what the application guarantees,
what it does not, and what to do when something goes wrong.

## Guarantees

1. **Nothing moves without your approval.** Organization always shows a preview. The only
   path that moves files unattended is automatic organization in Automation, which must be
   switched on explicitly, after a confirmation dialog.
2. **Dry runs change nothing.** A dry run performs the same validation as a real run,
   creates no folders, moves no files and writes no history.
3. **Existing files are never overwritten.** Each move is created as a new hard link and
   only then is the original removed. Creating a link fails atomically if the destination
   exists. When hard links are unavailable, Windows uses its non-replacing rename primitive,
   while POSIX fails closed and leaves the source untouched rather than risking an overwrite
   (see *Limitations*).
4. **A failed move never deletes a file it did not create.** If the original cannot be
   removed after the link was created, the extra link is removed again - but only after it
   has been proved, by device and inode, to still be the link this operation created *and*
   to still refer to the same file object as the source. If another program replaced that
   path in the meantime, or the filesystem reports no file identifier, the copy is kept and
   the failure reports both paths. An extra link is recoverable; a file another program just
   wrote is not.
5. **Collisions are resolved by a rule you choose.** The default renames the incoming file to
   `name (2).ext`, `name (3).ext`, and so on. The alternative skips the file and reports it.
6. **Sources are re-checked immediately before each move.** The file must still exist, be a
   regular file (not a link), have the same size and modification time, and carry the same
   file identity as when the preview was built. Otherwise it is skipped and reported. The
   check runs again after the destination folder has been created, and the move itself
   refuses a source that is no longer a regular file or no longer carries the recorded
   identity, so a path swapped between check and use cannot be followed.
7. **Destinations stay inside the selected folder.** Destination folders are plain names
   validated against Windows reserved names and invalid characters. Path containment is
   checked after resolving links.
8. **Symbolic links and junctions are never followed.** They are counted and skipped during
   scans, so the tool cannot be led outside the folder you selected.
9. **Organized folders are not re-sorted.** Files already inside a destination folder are
   not moved again, so recursive scans do not churn your sorted tree.
10. **Every outcome is recorded.** Each move, skip and failure is written to the history
   database with its reason, and each batch has a final status (`completed`, `partial`,
   `cancelled`, or `interrupted` after a crash).
11. **Undo is guarded.** A batch is reversed only when every outstanding move can be reversed
    safely. The checks are:
    - the organized file still exists, is a regular file, and has the recorded size,
      modification time and file identity;
    - the original location is free;
    - the original folder is still a real folder inside the batch folder.

    If any check fails, **nothing is moved** and the reasons are listed. Undo also removes
    folders that the batch created, but only if they are empty.
12. **Duplicates are reported, never deleted.** Two files are duplicates only if their SHA-256
    digests match. Files that change while being read are excluded, and hard links to one
    file are not counted as recoverable space.
13. **Least privilege.** The application never requests administrator rights and never runs
    shell commands built from file names.
14. **Privacy.** No network access, no telemetry, no cloud services and no reading of file
    contents for classification (see the README privacy statement).
15. **System locations are protected.** A drive or filesystem root, and any folder inside an
    operating-system directory (for example `C:\Windows`, `C:\Program Files`, `/etc`, `/usr`),
    is refused as a scan root. Because Organize, Duplicate Finder, Large Files, and Automation
    all scan through the same entry point, the refusal applies everywhere - a protected folder
    cannot be organized, watched, or duplicate-scanned. Ordinary user folders are unaffected.

## Limitations

- **No multi-file transaction.** Filesystems cannot commit several renames together. A batch
  can therefore end `partial`: some files moved, some did not. History shows exactly which,
  and each file is independently recorded.
- **Classification is by extension only.** A file named `invoice.jpg` that is really a PDF
  is classified as an image. The tool does not inspect contents to decide.
- **Cross-volume moves.** Organization destinations are always inside the selected folder, so
  moves are same-volume by construction. If a filesystem operation nevertheless reports a
  cross-device error (or hard links are unavailable), the move fails and the source is left
  exactly where it was: the source is only ever removed after the destination link has been
  created and verified to be the same file object, so an interrupted or impossible move cannot
  strand a file between volumes.
- **Filesystems without hard-link or file-ID support.** On POSIX volumes that do not support
  hard links (for example some FAT32, exFAT or network mounts), `move_no_overwrite` refuses
  the move and leaves the source in place rather than falling back to `os.rename`, which on
  POSIX could overwrite a file created concurrently. On Windows volumes without hard links,
  `os.rename` is used because the Win32 rename call fails if the destination exists. Similarly,
  if a filesystem provides no non-zero file identifier via `os.lstat` or an open file handle,
  replacement checks fall back to comparing file size and modification timestamp.
- **Undo is not a time machine.** It reverses moves only. It does not restore files you edited,
  renamed or deleted afterwards, and it refuses rather than guessing.
- **Monitoring is polling-based.** Changes are picked up within the configured interval
  (default five seconds). Monitoring runs only while the application is open.
- **Hidden and temporary files are excluded by default** (`.*`, `*.tmp`, `*.part`,
  `*.crdownload`, `~$*`, `desktop.ini`, `Thumbs.db`). You can change these in Settings.

## If something goes wrong

**A batch finished `partial`.** Open *History*, select the batch and read the *Error / reason*
column. Failed items usually have a cause you can fix (a file open in another program, a
permission problem). Then either organize the remaining files again from a fresh scan, or
undo the batch.

**Undo says a file was modified, moved or the original location is occupied.** Nothing was
changed. Resolve the conflict manually (move the file back yourself or rename whatever now
occupies the original path), then try again.

**The application closed during a batch.** On the next start, the batch is marked `interrupted`,
and any operation that was still in progress is marked `interrupted` with the message
"Application stopped before this operation finished; check the file locations manually."
Open *History*, select the batch, and press **Recovery guidance…**: it checks where the file
actually is now and states the safe next step for each one -
- at the original location only: the move never happened, nothing was lost;
- at the new location only: the move finished but was not recorded, so Undo cannot reverse it;
- at both: the interrupted move left two copies, and both paths are named so you can compare
  and delete one yourself (neither is deleted for you);
- at neither: another program moved, renamed or deleted it, and you should locate it before
  running anything else.

Operations that had already finished are unaffected. Guidance is also shown automatically
when you select a batch that ended `running`, `interrupted`, `partial` or `cancelled`.

**The history database is damaged.** It is renamed to `history.corrupt-<timestamp>.sqlite3`
in the data folder and a new history is started. The corrupt copy is kept so it can be
inspected. Your organized files are not affected. Undo is not available for batches recorded
in the damaged copy.

**I want to stop monitoring.** Press *Stop* on the Automation page, or remove the folder from
the list. Closing the application also stops monitoring. Stop is immediate: the request
reaches the scan and the move batch, so a poll already in progress moves nothing further.
If the folder is large and the current check has not finished, the status shows *Stopping*
for a moment; while it does, starting again is refused rather than running a second watcher,
and the status becomes *Stopped* on its own once the check has finished.

## Reporting a safety problem

If you find a way for the tool to move, overwrite or delete a file outside the folder you
selected, or to overwrite an existing file, please report it privately through GitHub's
security advisory feature instead of a public issue.
