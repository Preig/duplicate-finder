# Duplicate Finder

A local **Windows and Mac desktop app** for finding identical files and choosing which copies to remove.

## Download and run — no Python installation

Download an app from [Releases](https://github.com/Preig/duplicate-finder/releases/latest), extract its ZIP, and open it:

| Computer | Download | Open after extracting |
| --- | --- | --- |
| Windows 10/11, 64-bit Intel/AMD | `Duplicate-Finder-Windows-x64.zip` | `Duplicate Finder.exe` |
| Apple Silicon Mac (M-series) | `Duplicate-Finder-macOS-Apple-Silicon.zip` | `Duplicate Finder.app` |
| Intel Mac | `Duplicate-Finder-macOS-Intel.zip` | `Duplicate Finder.app` |

The apps include Python, Tk, and the required libraries. **Users do not need Python, a package manager, or other supporting software.** On Mac you can drag the extracted app to Applications. Mac release builds target **macOS 15 (Sequoia) or later**; older releases have not been validated.

These are unsigned community builds. Mac apps are ad-hoc signed, without an Apple Developer identity or Apple notarization. Windows or macOS may require approval before first launch. This project does not disable or change OS security settings.

## Features

- Scan one or more folders, including subfolders.
- Find exact duplicates using size, 64 KB samples, full SHA-256 hashes, and byte-for-byte verification.
- Group results by **matching content** or **folder**.
- Choose **none, one, several, or all** files to remove; selections persist across grouping and filtering.
- Review every selected path before sending files to **Recycle Bin on Windows** or **Trash on Mac**.
- Explicit acknowledgement before removing every scanned copy of a match.
- Changed-file checks before removal, with no permanent-delete fallback.
- Reveal files in Explorer or Finder; copy paths; export results and issues to CSV.

See [INSTRUCTIONS.txt](INSTRUCTIONS.txt) for detailed controls and scan coverage.

## Privacy and safeguards

The app runs locally without uploads, credentials, telemetry, or server requests. It scans only folders you select. CSV reports are written only where you choose to save them. Nothing is selected for removal after a scan.

Cloud-only files are skipped to avoid downloading them. Links and junctions are not followed; hard links are counted once. Common system/development folders and empty files are excluded by default. Changed or unreadable files are reported. Results are a snapshot; re-scan after outside changes.

Files in synced folders may also be removed from other synced devices. Trash / Recycle Bin contents still occupy storage until emptied. Extra logical size is not a guarantee of reclaimable disk space.

## Validation and builds

GitHub Actions builds on Windows, Apple Silicon Mac, and Intel Mac. Each release requires:

1. The source test suite, including native Trash / Recycle Bin tests on disposable fixtures.
2. A standalone app test with Python environment variables removed and Python excluded from PATH.
3. A real Tk window, exact matching, folder grouping, selection controls, and native recycling inside the bundled app.
4. ZIP integrity validation and a published SHA-256 checksum.

Reports are included with the downloads. These checks do not replace a full manual desktop review or certify antivirus reputation, Apple notarization, or compatibility with older Mac versions.

## Developers only

Python is needed to work on the source or build the app, not to use a downloaded release. Build each target on its native OS:

```text
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python scripts/build.py
```

The tests generate disposable fixtures and send some to Trash / Recycle Bin. They do not scan personal folders. Desktop integration tests require a normal Windows or Mac desktop environment.

The source can also be run with `python duplicate_finder.pyw`; the Windows `.cmd` launcher is retained for source development.
