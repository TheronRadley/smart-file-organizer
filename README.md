# Smart File Organizer

A Windows desktop app for sorting, finding and safely cleaning up files. It previews every
change before it happens, never overwrites or deletes your files, and records what it did so
the most recent organization can be undone.

> **Status: early development (`0.1.0.dev0`, no release yet).** The core logic is covered by an
> automated test suite. The graphical interface is built and exercised on a Windows runner in CI —
> every page opens and the window closes — but it has not been reviewed visually by a person, and
> the packaged executable has not been produced yet. See [Known limitations](#known-limitations).

## Screenshots

_Screenshots have not been captured yet._

- `[Screenshot placeholder: Dashboard]`
- `[Screenshot placeholder: Organize Files preview]`
- `[Screenshot placeholder: Duplicate Finder]`
- `[Screenshot placeholder: History and guarded undo]`

## Features

- **Organize Files** — scan a folder, build a preview, run a dry run, then apply selected moves.
  Sort by type (ten categories plus Other), extension, or modified month. Collisions are renamed
  (`name (2).ext`) by default, or skipped. Existing files are never overwritten.
- **Guarded undo** — a batch is reversed only if every file is still where it was organized and its
  original location is free. Otherwise nothing changes and the reasons are shown.
- **Duplicate Finder** — groups files by size, then partial and full SHA-256 hash. Reports
  recoverable space. Never deletes anything.
- **Large Files** — lists the biggest files above a size threshold, filterable by extension.
  Flags results as incomplete if the scan was cancelled or some items could not be read.
- **Automation** — watches chosen folders. Preview-first by default; automatic organization is an
  explicit opt-in. Waits until files stop changing, handles each file once, and supports pause,
  resume and stop.
- **History** — every real operation is recorded with its outcome and reason (dry runs are not).
  Clearing history never touches your files. Batches that did not finish cleanly come with
  **recovery guidance**: where the file is now, and the safest next step.
- **Reports** — export history, scan results and duplicate reports to CSV (UTF-8 with BOM, so
  spreadsheets read non-ASCII paths) or JSON, for auditing and for keeping a record off the machine.
- **Dashboard** — shows real results from your scans and history.
- **Settings** — collision rule, organization method, category folder names, exclusions, extension
  overrides, watch options, dark (default) or light theme.
- **Safety by design** — symbolic links and junctions are never followed, destinations are validated
  against Windows naming rules, and files are re-checked immediately before each move.

Details: [User guide](docs/USER_GUIDE.md) · [Safety](docs/SAFETY.md) · [Architecture](docs/ARCHITECTURE.md)

## Requirements

- Windows 10 or 11 (the target platform; the test suite also runs on Linux and macOS)
- Python 3.12 or newer, if running from source
- No administrator rights are needed.

## Install and run from source

PowerShell:

```powershell
git clone https://github.com/TheronRadley/smart-file-organizer.git
cd smart-file-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Equivalent on macOS/Linux: `python3.12 -m venv .venv && source .venv/bin/activate`, then the same
`pip install` and `python app.py` commands.

You can also run `python -m smart_file_organizer` from the `src` folder, or install the project
with `python -m pip install .` to get the `smart-file-organizer` command.

## Run the tests and linter

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
ruff check .
ruff format --check .
```

The tests use temporary folders only. They never read or write your real files or settings.

CI also runs a GUI startup smoke test on Windows (the unit tests never import tkinter) and
audits the pinned dependencies for known vulnerabilities with `pip-audit`. Manual Windows
checks that cannot be automated are listed in
[docs/WINDOWS_SAFETY_CHECKLIST.md](docs/WINDOWS_SAFETY_CHECKLIST.md).

## Build a Windows executable

```powershell
python -m pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --windowed --name SmartFileOrganizer --paths src --collect-all customtkinter app.py
```

The program is written to `dist\SmartFileOrganizer\SmartFileOrganizer.exe`. The
`Windows build` GitHub Actions workflow runs the same command and uploads the folder as an artifact.
The build has not yet been produced or tested on Windows (see [Known limitations](#known-limitations)).

## Where data is stored

| Item | Location |
| --- | --- |
| Settings | `%APPDATA%\SmartFileOrganizer\settings.json` |
| History | `%APPDATA%\SmartFileOrganizer\history.sqlite3` |
| Log | `%APPDATA%\SmartFileOrganizer\app.log` |

Set the `SFO_DATA_DIR` environment variable to use a different folder.

## Privacy

Everything runs on your computer. The app makes no network requests, collects no telemetry, and
does not read file contents to classify them (classification uses file names and extensions).

## Known limitations

- The GUI is exercised by an automated smoke test on a Windows runner (it opens every page, walks
  the navigation and closes), so build and import errors are caught. Nobody has reviewed the layout
  visually yet, and the manual checks in `docs/WINDOWS_SAFETY_CHECKLIST.md` have not been run.
- Classification is by extension only; a misnamed file is organized by its extension.
- A batch is applied one file at a time. If a move fails, the batch ends as *partial*; History shows which files moved.
- Undo reverses moves only and refuses rather than guessing when anything has changed.
- Folder monitoring polls (default every 5 seconds) and runs only while the app is open.
- Images are not thumbnailed or previewed; metadata includes dimensions read from headers.
- No installer, code signing, or published release exists yet.

See [docs/SAFETY.md](docs/SAFETY.md) for the full list of guarantees and limits.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
