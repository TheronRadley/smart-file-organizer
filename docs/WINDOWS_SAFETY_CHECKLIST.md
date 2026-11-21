# Windows safety checklist (manual)

These checks supplement CI's source startup and packaged preview/apply/undo smoke tests. Run
them on a Windows 10/11 desktop before a release. Report unexpected file changes through
GitHub's private security advisory.

Core tests run on Ubuntu, macOS and Windows in CI. This checklist adds real-volume behavior,
desktop interactions, and abrupt shutdown that the automated smoke does not cover.

## Preparation

1. Install the packaged build (`SmartFileOrganizer.exe`) rather than running from source,
   so the frozen executable and its bundled Tcl/Tk are what you test.
2. Create a scratch folder, for example `C:\SFO-Test`, with a mix of files: a few
   documents, images, an archive, a file with a long name (200+ characters), a file with
   spaces and Unicode in its name, an empty file, and a file larger than 4 GB if the disk
   allows it.
3. Start the app with `SFO_DATA_DIR` set to a separate scratch profile so you can inspect
   `history.sqlite3`, `settings.json` and `app.log` afterwards.

## 1. Stop during a scan

- [ ] Add a large folder (tens of thousands of files) to Automation, enable **Organize
      automatically**, press **Start**, and press **Stop** while the activity log still
      shows scanning.
- [ ] Expected: the status changes to *Stopping* and then to *Stopped* on its own; the UI stays
      responsive. A file operation already in progress may finish. Pressing **Start monitoring**
      again while *Stopping* does not create a second watcher.
- [ ] Repeat ten times; confirm the folder count in History does not grow unexpectedly and
      that no file appears in two places.

## 2. Shutdown during a move

- [ ] Start an organize batch over a folder with many large files and close the application
      (window close button) while files are still moving. If needed, end the process from
      Task Manager to simulate a crash.
- [ ] A cooperative close marks the batch cancelled when applicable. After a forced exit and
      restart, unfinished operations appear under **Recovery guidance…** even if their batch
      was previously finalized. Check both locations and the verification status for each file.
- [ ] Verify no file was lost: every file is at its original location, its new location, or
      both - and never nowhere.

## 3. Occupied destinations

- [ ] In the destination folder, open a file in an editor and hold the lock, then organize a
      folder containing a file with the same name.
- [ ] Expected: the incoming file is renamed (`name (2).ext`) or skipped per the collision
      rule; the locked file's contents are unchanged.
- [ ] With the collision rule set to *skip*, confirm the incoming file is left in place and
      reported as skipped.

## 4. Junctions and symbolic links

- [ ] Create a junction inside the scanned folder pointing outside it
      (`mklink /J C:\SFO-Test\junction C:\Windows\Temp`) and a symbolic link to a file
      outside it (`mklink C:\SFO-Test\link.txt C:\outside.txt`).
- [ ] Expected: both are counted as skipped links and never followed; nothing outside
      `C:\SFO-Test` is created, moved or deleted.
- [ ] Make a destination *category folder* a junction pointing outside, then organize.
      Expected: the operation is refused with "Destination folder is blocked by a file or
      link" and nothing is written through the junction.

## 5. Unusual file names

- [ ] Organize files whose names contain spaces, Unicode, emoji, trailing dots or spaces,
      reserved device names (`CON.txt`, `NUL`), very long names and paths near the 260
      character limit.
- [ ] Expected: each file is moved intact or reported with a clear reason. No name is
      silently truncated, and no file ends up in the wrong folder.

## 6. Removable and network volumes

- [ ] Repeat a small organize on a FAT32/exFAT USB stick, and on a network share (SMB).
- [ ] Expected on Windows: files are moved using the non-replacing rename and never
      overwrite an existing file. If a move fails, the source is still there and the
      failure reason is shown in History.
- [ ] If the volume refuses a safe same-volume rename or directory identity cannot be verified,
      confirm the source is unchanged. Confirm the app does not fall back to copy/delete.

## 7. Recovery from History

- [ ] For every failure produced above, open History, select the batch and confirm the
      *Error / reason* column explains it without a stack trace.
- [ ] Confirm **Undo** reverses a completed batch, refuses a batch whose files changed
      afterwards (changing nothing at preflight), and can retry remaining recorded moves after
      you resolve the cause of a partial restore. Check Recovery for any unresolved restore.
- [ ] Export the history to CSV and to JSON and confirm both open correctly and show the
      same operations, including non-ASCII paths. Pick an existing report and decline the
      replacement prompt; it must be unchanged.

## 8. Interface and lifecycle

- [ ] Walk every page, start and cancel a scan, start and stop monitoring, switch the theme,
      and close the window. Expected: no crash, no frozen window, and no modal dialog left
      behind. `app.log` must contain no unhandled traceback.
- [ ] Confirm monitoring stops when the window is closed (no `SmartFileOrganizer.exe`
      process remains in Task Manager).

## Recording a run

Note the Windows build, the application version, the date, and the outcome of each item.
Where an item fails, keep `app.log` and the history database and report them privately.

## Automated safety coverage

CI covers destination collisions at publication, stale approvals and reconfiguration, an in-place
source edit while journaling, protected data directories, unresolved move history, and shutdown
coordination. Manual testing should focus on real Windows junctions, file locks and filesystem
semantics not reproduced in fixtures.
