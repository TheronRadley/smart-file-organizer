# User Guide

This guide walks through each page of Smart File Organizer. For the reasons behind the
safety rules, read [SAFETY.md](SAFETY.md).

## Starting the app

- **Installed build:** run `SmartFileOrganizer.exe` (see the README for how to build it yourself).
- **From source:** `python app.py`, from the project folder, inside your virtual environment.

The sidebar lists the pages: Dashboard, Organize Files, Duplicate Finder, Large Files, Automation,
History, Settings and About. The app opens in the dark theme unless you change it in Settings.

## Scanning a folder

Most pages work from a *scan* of one folder. Choose the folder with the folder picker, then scan.
You can cancel a scan at any time; the results shown are then marked incomplete.

- **Include subfolders** controls whether the scan goes into nested folders (set in Settings
  as the default, and changeable on the page).
- Hidden files, temporary files and common junk (`.*`, `*.tmp`, `*.part`, `*.crdownload`, `~$*`,
  `desktop.ini`, `Thumbs.db`) are excluded by default, along with the folders `.git`,
  `node_modules`, `__pycache__`, `.venv` and `venv`. Change these lists in Settings.
- Symbolic links and junctions are never followed. They are counted and listed as skipped.
- Unreadable items are reported as issues with a plain-language reason. The scan keeps going.

## Dashboard

Shows real figures from the most recent scan and from your history: file counts and sizes,
category distribution, the largest files in the current scan, and recent batches. Nothing on this
page is sample data.

## Organize Files

1. Choose a folder and scan it.
2. Choose how to organize, in Settings: **by type** (Documents, Images, Videos, Audio, Archives,
   Code, Spreadsheets, Presentations, Executables, Fonts, and Other), **by extension**, or **by date**
   (modified month). Destination folder names can be changed in Settings.
3. Press **Build preview**. Every file gets a row with its status:
   - *ready*: will be moved to the shown destination, possibly with a new name;
   - *skipped*: left where it is, with the reason (for example, already in the right place, in a
     disabled category, or the file is inside an organized folder).
4. Use the search box, the extension filter and the minimum/maximum size filter to narrow the list.
   **Select all ready** selects every ready row.
5. Press **Dry run** to see the result without changing anything. A dry run writes nothing to
   History and creates no folders.
6. Select the rows you want, press **Apply selected…**, and confirm. Each file is moved one at a time,
   and the outcome for each is shown as success, skipped or failed.

**Collisions.** If a file with the same name already exists at the destination, the default
(*rename*) saves the new file as `name (2).ext`, `name (3).ext` and so on. The other option, set in
Settings, is *skip*. No existing file is ever overwritten.

**Partial batches.** If some moves fail, the batch finishes as *partial*. The files that succeeded
stay moved, and the failures explain why. You can fix the cause (for example, close the program
that has the file open) and organize again from a fresh scan.

**Cancelling.** Cancelling during apply stops before the next file. Files already moved stay moved,
and the batch is recorded as *cancelled*.

## Duplicate Finder

Press **Find duplicates** after scanning. The tool groups files by size first, then compares a
partial hash, then a full SHA-256 hash. Only files with identical content are listed as duplicates.

- The report shows each group, the copies in it, and the space that could be recovered.
- Hard links to the same data count once.
- Files that change while being read are left out and listed as issues.
- **The app never deletes duplicates.** Decide what to remove yourself, in Explorer.
- **Open file location** opens the selected file's folder in Explorer.
- **Mark / unmark for review** flags files you want to look at later. It changes nothing on disk.
- **Copy review list** copies the flagged files' paths to the clipboard.

## Large Files

Set a minimum size (the default comes from Settings) and optional extensions such as `mkv, iso, zip`,
then press **Scan folder**. Results are sorted largest first. If the scan was cancelled or some
items could not be read, the list is marked *incomplete*. Like duplicates, this page only reports
files; it never deletes them.

Select a file and use **Open containing folder** to see it in Explorer.

## Automation (folder monitoring)

Automation watches folders you add and handles new or changed files once they stop changing.

1. Press **Add folder…** and choose a folder. Tick **Include subfolders** if you want nested folders watched.
2. Press **Start monitoring**. The status shows Running, Paused or Stopped.
3. Files are only considered after their size and modification time have been stable for about
   10 seconds, and the folder is checked about every 5 seconds.
4. By default the watcher is in **preview mode**: stable files appear under *Waiting for approval*.
   Press **Apply waiting files…** to move the ones you approve. A file that changed after it was
   listed is not moved; it is listed again when it becomes stable.
5. **Organize automatically (no preview)** is off by default. Turning it on asks for confirmation.
   With it on, stable files are moved without asking, under the same collision, exclusion and
   revalidation rules, and every move is recorded and can be undone.
6. **Pause** and **Resume** keep the folder list. **Stop** ends monitoring. **Remove selected** removes a folder.
7. Monitoring only runs while the app is open. Closing the app stops it.

The activity log on the page lists what the watcher did during this session.

## History

Every real organize operation is listed as a batch, with its status (*completed*, *partial*,
*cancelled*, *interrupted* or *undone*). Select a batch to see each operation with its
source, destination and **Error / reason**. Dry runs are not recorded.

- **Undo selected batch…** reverses a batch. Undo is guarded:
  - It lists the files it would move back. If any file was edited, renamed, moved or deleted
    afterwards, or its original location is now occupied, **undo is refused and nothing is changed**.
    The page lists the reasons.
  - When it runs, it removes folders the batch created, but only if they are empty.
- **Clear history…** deletes the history records only. It never deletes or moves your files.
- **Refresh** reloads the list.

## Settings

- **Theme:** dark (default) or light.
- **Collision rule:** rename (default) or skip.
- **Organize by:** type, extension or date.
- **Include subfolders by default**.
- **Excluded folders and patterns**, one per line. Empty lists fall back to the defaults.
- **Category folders:** the destination name for each category.
- **Disabled categories:** categories that are never moved.
- **Extension overrides:** assign a specific extension to a category.
- **Large-file minimum (MB)** and **watch interval** for monitoring.
- **Save settings** writes `settings.json`. **Restore defaults…** resets everything.
- **Open data folder** opens the folder holding settings, history and the log.

Settings are saved in your user profile, normally `%APPDATA%\SmartFileOrganizer\`.

## About

Shows the version, the license, the known limits listed in the app, and links to these documents.

## Where things are stored

| File | Purpose |
| --- | --- |
| `settings.json` | Your settings. Safe to delete; defaults return on next start. |
| `history.sqlite3` | Batch and operation history. Clearing it in the app is the supported way to empty it. |
| `app.log` | Diagnostic log (rotating). Share it when reporting a bug, after removing personal names. |

If the history database is damaged, it is renamed to `history.corrupt-<timestamp>.sqlite3` and a new
one is started. Your files are not affected.

## Troubleshooting

- **"Undo refused"**: the reasons list which files changed. Restore those by hand, then try again.
- **Preview looks out of date**: scan again. A preview is a snapshot, and every move is re-checked before it runs.
- **A file is skipped as "in the way"**: a file or link exists where a destination folder should be.
  Rename or move that item, then rebuild the preview.
- **Nothing happens in Automation**: check the folder is in the list and monitoring is Running. Files must
  be stable for about 10 seconds first.
