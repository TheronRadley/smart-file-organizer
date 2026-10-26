<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo.svg" alt="Smart File Organizer" width="520">
</picture>

<br>

**Sort your files — with a preview, a safety net, and an undo button.**

Smart File Organizer is a Windows desktop app that organizes messy folders
without ever overwriting, deleting, or surprising you. Every move is previewed
first, recorded in history, and reversible.

<br>

[![Tests](https://github.com/TheronRadley/smart-file-organizer/actions/workflows/tests.yml/badge.svg)](https://github.com/TheronRadley/smart-file-organizer/actions/workflows/tests.yml)
[![Windows build](https://github.com/TheronRadley/smart-file-organizer/actions/workflows/build-windows.yml/badge.svg)](https://github.com/TheronRadley/smart-file-organizer/actions/workflows/build-windows.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)](#requirements)
[![Version](https://img.shields.io/badge/Version-0.1.0.dev0-6366F1)](CHANGELOG.md)
[![License: Source-Available](https://img.shields.io/badge/License-Source--Available%20%C2%B7%20Free%2C%20No%20Sale-A855F7)](LICENSE)

<br>

[User Guide](docs/USER_GUIDE.md) · [Safety Guarantees](docs/SAFETY.md) · [Architecture](docs/ARCHITECTURE.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## Why Smart File Organizer?

Most organizers move first and explain later. This one is built the other way around:

- **Preview before anything moves.** Scan a folder, inspect the exact plan, run a dry run, then apply only the moves you selected.
- **Nothing is ever overwritten or deleted.** Collisions are renamed (`name (2).ext`) or skipped — your existing files are never replaced.
- **Every move can be undone.** Batches are recorded in a local history and reversed only when every file is exactly where it was left; otherwise nothing changes and you are told why.
- **Private by design.** Everything runs on your machine. No accounts, no network requests, no telemetry.

## Features

| | |
|---|---|
| **Organize** | Sort by type (10 categories + Other), extension, or modified month — with per-file selection and collision handling |
| **Duplicate Finder** | Groups identical files by size and SHA-256, reports recoverable space, and never deletes anything |
| **Large Files** | Surfaces the biggest files above a threshold, with honest incomplete-result warnings |
| **Automation** | Optional folder monitoring — preview-first by default, waits for files to finish copying, stop means stop |
| **History & Recovery** | Full audit trail, guarded undo, and step-by-step guidance for batches interrupted mid-run |
| **Reports** | Export history, scans, and duplicate reports to spreadsheet-safe CSV or JSON |

## Safety at a glance

- Symbolic links and Windows junctions are never followed
- Source files are re-validated by identity immediately before each move
- Exports refuse to silently overwrite an existing report
- A stopped watcher moves nothing further — and never runs two workers

The full guarantees, limits, and threat model live in **[docs/SAFETY.md](docs/SAFETY.md)**.

## Quick start

**Requirements:** Windows 10 or 11 · Python 3.12+ (from source) · no administrator rights

```powershell
git clone https://github.com/TheronRadley/smart-file-organizer.git
cd smart-file-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

<sub>macOS/Linux equivalent: `python3.12 -m venv .venv && source .venv/bin/activate`, then the same `pip install` and `python app.py`. You can also `pip install .` to get the `smart-file-organizer` command.</sub>

## Building the Windows executable

```powershell
python -m pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --windowed --name SmartFileOrganizer --paths src --collect-all customtkinter app.py
```

Output: `dist\SmartFileOrganizer\SmartFileOrganizer.exe` — the same command runs in the **Windows build** GitHub Actions workflow.

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest            # tests use temporary folders only — never your real files
ruff check .
ruff format --check .
```

CI additionally runs a Windows GUI startup smoke test and a `pip-audit` dependency audit. Manual checks that need a real Windows desktop are listed in [docs/WINDOWS_SAFETY_CHECKLIST.md](docs/WINDOWS_SAFETY_CHECKLIST.md).

## Where your data lives

| Item | Location |
|---|---|
| Settings | `%APPDATA%\SmartFileOrganizer\settings.json` |
| History | `%APPDATA%\SmartFileOrganizer\history.sqlite3` |
| Log | `%APPDATA%\SmartFileOrganizer\app.log` |

Set `SFO_DATA_DIR` to use a different folder.

## Status

Early development (`0.1.0.dev0`) — no installer, code signing, or published release yet. The core logic is covered by an automated test suite on Windows, Linux, and macOS runners; the interface is smoke-tested in CI but has not yet had a full visual review, and the manual Windows safety checklist is still to be run. Honest, specific status — no surprises, just like the app.

## License

**Free to use, study, modify, and fork — never to sell, and never to pass off as your own.**

Smart File Organizer is source-available under the [Smart File Organizer License](LICENSE). Anyone may use, copy, modify, and redistribute it free of charge with attribution. Selling it (or any fork of it), claiming authorship, impersonating the author, or using the name and logo to imply official status is prohibited.

---

<div align="center">
<sub>Built with Python & CustomTkinter · Runs 100% locally · Your files stay yours</sub>
</div>
