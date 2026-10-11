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
   only then is the original removed. Creating a link fails if the destination exists. On
   file systems without hard links, the fallback checks for the destination first and then
   renames; a small race window exists there (see *Limitations*).
4. **Collisions are resolved by a rule you choose.** The default renames the incoming file to
   `name (2).ext`, `name (3).ext`, and so on. The alternative skips the file and reports it.
5. **Sources are re-checked immediately before each move.** The file must still exist, be a
   regular file (not a link), have the same size and modification time, and carry the same
   file identity as when the preview was built. Otherwise it is skipped and reported.
6. **Destinations stay inside the selected folder.** Destination folders are plain names
   validated against Windows reserved names and invalid characters. Path containment is
   checked after resolving links.
7. **Symbolic links and junctions are never followed.** They are counted and skipped during
   scans, so the tool cannot be led outside the folder you selected.
8. **Organized folders are not re-sorted.** Files already inside a destination folder are
   not moved again, so recursive scans do not churn your sorted tree.
9. **Every outcome is recorded.** Each move, skip and failure is written to the history
   database with its reason, and each batch has a final status (`completed`, `partial`,
   `cancelled`, or `interrupted` after a crash).
10. **Undo is guarded.** A batch is reversed only when every outstanding move can be reversed
    safely. The checks are:
    - the organized file still exists, is a regular file, and has the recorded size,
      modification time and file identity;
    - the original location is free;
    - the original folder is still a real folder inside the batch folder.

    If any check fails, **nothing is moved** and the reasons are listed. Undo also removes
    folders that the batch created, but only if they are empty.
11. **Duplicates are reported, never deleted.** Two files are duplicates only if their SHA-256
    digests match. Files that change while being read are excluded, and hard links to one
    file are not counted as recoverable space.
12. **Least privilege.** The application never requests administrator rights and never runs
    shell commands built from file names.
13. **Privacy.** No network access, no telemetry, no cloud services and no reading of file
    contents for classification (see the README privacy statement).

## Limitations

- **No multi-file transaction.** Filesystems cannot commit several renames together. A batch
  can therefore end `partial`: some files moved, some did not. History shows exactly which,
  and each file is independently recorded.
- **Classification is by extension only.** A file named `invoice.jpg` that is really a PDF
  is classified as an image. The tool does not inspect contents to decide.
- **Hard-link fallback race.** On volumes without hard-link support (for example some FAT32,
  exFAT or network shares), a move falls back to a rename after an existence check. Another
  program creating the same name in the instant between the check and the rename could be
  overwritten on POSIX systems. On NTFS, which Windows PCs normally use, the hard-link path is
  used and this race does not arise.
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
Look at the source and destination of those files in *History*, and move them back by hand if
needed. Operations that had already finished are unaffected.

**The history database is damaged.** It is renamed to `history.corrupt-<timestamp>.sqlite3`
in the data folder and a new history is started. The corrupt copy is kept so it can be
inspected. Your organized files are not affected. Undo is not available for batches recorded
in the damaged copy.

**I want to stop monitoring.** Press *Stop* on the Automation page, or remove the folder from
the list. Closing the application also stops monitoring.

## Reporting a safety problem

If you find a way for the tool to move, overwrite or delete a file outside the folder you
selected, or to overwrite an existing file, please report it privately through GitHub's
security advisory feature instead of a public issue.
