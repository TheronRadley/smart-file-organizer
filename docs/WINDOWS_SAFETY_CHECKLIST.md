# Windows safety checklist (manual)

These checks need a real Windows 10/11 desktop and cannot be automated in CI. Run them
before a release. Each item lists what to do and what must happen; anything else is a
safety bug and should be reported through GitHub's private security advisory.

Automated coverage lives in `tests/` and runs on Ubuntu and Windows on every push. This
checklist covers what those tests cannot reach: a real GUI session, real filesystems
without hard-link support, and killing the application mid-operation.

## Preparation

1. Install the packaged build (`SmartFileOrganizer.exe`) rather than running from source,
   so the frozen executable and its bundled Tcl/Tk are what you test.
2. Create a scratch folder, for example `C:\SFO-Test`, with a mix of files: a few
   documents, images, an archive, a file with a long name (200+ characters), a file with
   spaces and Unicode in its name, an empty file, and a file larger than 4 GB if the disk
   allows it.
3. Open the application data folder (`%APPDATA%\SmartFileOrganizer`) so you can inspect
   `history.sqlite3`, `settings.json` and `app.log` afterwards.

## 1. Stop during a scan

- [ ] Add a large folder (tens of thousands of files) to Automation, enable **Organize
      automatically**, press **Start**, and press **Stop** while the activity log still
      shows scanning.
- [ ] Expected: the status changes to *Stopping* and then to *Stopped* on its own. No file
      is moved after *Stop* was pressed. Pressing **Start** again while it shows *Stopping*
      does not create a second watcher (the log says "Still stopping…").
- [ ] Repeat ten times; confirm the folder count in History does not grow unexpectedly and
      that no file appears in two places.

## 2. Shutdown during a move

- [ ] Start an organize batch over a folder with many large files and close the application
      (window close button) while files are still moving. If needed, end the process from
      Task Manager to simulate a crash.
- [ ] Restart the application. Expected: the batch is `interrupted`, the unfinished
      operations are marked `interrupted`, and **Recovery guidance…** on the History page
      names, for each one, where the file is and what to do next.
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

## 6. Filesystems without hard-link support

- [ ] Repeat a small organize on a FAT32/exFAT USB stick, and on a network share (SMB).
- [ ] Expected on Windows: files are moved using the non-replacing rename and never
      overwrite an existing file. If a move fails, the source is still there and the
      failure reason is shown in History.
- [ ] If any failure mentions a kept copy at the destination, confirm both paths in the
      message exist and that following the advice resolves it without data loss.

## 7. Recovery from History

- [ ] For every failure produced above, open History, select the batch and confirm the
      *Error / reason* column explains it without a stack trace.
- [ ] Confirm **Undo** reverses a completed batch, refuses a batch whose files changed
      afterwards (changing nothing), and that a partially undone batch can be completed by
      running Undo again.
- [ ] Export the history to CSV and to JSON and confirm both open correctly and show the
      same operations, including non-ASCII paths.

## 8. Interface and lifecycle

- [ ] Walk every page, start and cancel a scan, start and stop monitoring, switch the theme,
      and close the window. Expected: no crash, no frozen window, and no modal dialog left
      behind. `app.log` must contain no unhandled traceback.
- [ ] Confirm monitoring stops when the window is closed (no `SmartFileOrganizer.exe`
      process remains in Task Manager).

## Recording a run

Note the Windows build, the application version, the date, and the outcome of each item.
Where an item fails, keep `app.log` and the history database and report them privately.

## Automated race coverage (all platforms, CI)

The following are covered by deterministic tests on Windows, Linux, and macOS and do not
need manual repetition; they are listed so manual runs can focus on what automation cannot
reach (real reparse points, locked files, packaged executable):

- Source replaced before linking, after linking, and before unlinking (`tests/test_fileops.py`)
- Destination folder swapped for a symlink during journaling (`tests/test_adversarial_safety.py`)
- Automation approving a same-name, same-size, same-timestamp replacement (`tests/test_watcher.py`)
- Competing destination creation, cross-volume failure, shutdown with active workers
