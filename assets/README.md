# Assets

This folder is reserved for application artwork (icons, logos) used by the
PyInstaller build.

A transparent logo is provided at `assets/logo.svg`, with a dark-mode variant
at `assets/logo-dark.svg` (both used in the README). No Windows icon is
bundled yet. To add one, place a Windows icon at
`assets/app.ico` and pass `--icon assets/app.ico` to PyInstaller (see
`docs/` and the README "Build a Windows executable" section). Only commit
artwork you have the right to distribute under the project's license (see `LICENSE`).
