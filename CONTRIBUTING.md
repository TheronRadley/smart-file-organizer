# Contributing to Smart File Organizer

Thank you for helping improve Smart File Organizer. Because this tool moves user files, changes
to filesystem code are held to a higher standard than most projects. Please read
[docs/SAFETY.md](docs/SAFETY.md) before touching `core/organizer.py`, `core/undo.py`,
`core/fileops.py` or `utils/paths.py`.

## Setting up

Requires Python 3.12 or newer.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
```

macOS/Linux: `python3.12 -m venv .venv && source .venv/bin/activate`, then the same `pip` command.

## Before you open a pull request

```bash
ruff format --check .
ruff check .
python -m pytest
```

CI runs the same commands on Windows and Linux.

## Guidelines

- **Core logic stays UI-free.** Modules under `src/smart_file_organizer/core` must not import
  `customtkinter` or `tkinter`. Pages call core functions; they do not reimplement them.
- **Tests use temporary directories only.** Never write tests that touch real user folders.
  Use the `workspace` and `database` fixtures in `tests/conftest.py`.
- **Every new filesystem behaviour needs a test,** including the failure path.
- **Never swallow exceptions silently.** Expected conditions are returned as results or
  reported as skipped/failed; unexpected ones are logged with `logger.exception`.
- **Keep user-facing messages plain.** No stack traces in dialogs.
- **Do not commit** databases, logs, `settings.json`, virtual environments, build output or
  personal files. `.gitignore` covers the usual cases.
- **Keep documentation honest.** If behaviour changes, update the README, the user guide and
  `CHANGELOG.md` in the same pull request.
- Follow the existing style: type hints on public functions, `pathlib` for paths, docstrings
  where they explain behaviour.

## Reporting bugs

Open an issue with your Windows version, the steps to reproduce, and the relevant lines from
the log file (`%APPDATA%\SmartFileOrganizer\app.log`). Please remove personal file names from
the log before posting.

Security-sensitive reports (for example, a way to make the tool move or overwrite files outside
the selected folder) should be reported privately through GitHub's security advisory feature
rather than in a public issue.
