# Duplicate Finder

A local Windows desktop app for finding identical files and choosing which copies to remove.

## Features

- Select one or more folders and scan their subfolders.
- Find exact duplicates using file sizes, 64 KB samples, full SHA-256 hashes, and byte-for-byte comparison.
- Group results by **matching content** or **folder**.
- Select **none, one, several, or all** files for deletion. Selection persists across grouping and filtering.
- Review all selected paths before moving files to the Windows Recycle Bin.
- Explicit acknowledgement when every scanned copy in a match is selected.
- Changed-file checks before recycling; no permanent-delete fallback.
- Filter paths, reveal files in Explorer, copy paths, and export results or issues to CSV.

## Run

Requires **Windows 10/11** and **Python 3.10+ with Tkinter**. No additional Python packages are required.

1. Use **Code → Download ZIP**, then extract the archive.
2. Double-click **Start Duplicate Finder.cmd**.
3. Add folders and choose **Scan folders**.

You can also launch from a terminal:

```powershell
python duplicate_finder.pyw
```

Read [INSTRUCTIONS.txt](INSTRUCTIONS.txt) for selection controls, exclusions, scan coverage, and deletion behavior.

## Privacy and deletion behavior

The app operates locally. It does not upload files, request credentials, contact a server, or include telemetry. CSV exports are written only to the location you select.

Nothing is selected for deletion after scanning. Deletion requires an explicit review and confirmation. Files are sent to the Recycle Bin; operations that would permanently delete a file are blocked. Files deleted from a synced folder may also be removed from other synced devices. Recycled files still occupy storage until the bin is emptied.

Cloud-only files are skipped to avoid downloading them. Symbolic links and directory junctions are not followed, and hard links are counted once. By default, common system and development folders and zero-byte files are excluded. Those options can be changed in the app.

Results are a snapshot. Re-scan after external changes. Extra logical size counts the bytes in copies beyond one per match; it does not measure guaranteed reclaimable disk space.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite contains 18 tests. Windows integration tests create disposable fixtures beneath the tests directory and send some of those fixtures to the Recycle Bin. They do not scan personal folders. Run on a normal Windows desktop for Recycle Bin access. Desktop and recycling tests are skipped on other operating systems.

Validation covers content matching, differing tails, a simulated hash collision, hard links, overlapping roots, exclusions, cancellation, Unicode paths, CSV export, folder grouping, selection persistence, all-copies acknowledgement, changed-file rejection, actual recycling, and widget bounds at two window sizes. Screenshot-based visual inspection has not been completed.

## Project files

| File | Purpose |
| --- | --- |
| `duplicate_finder.pyw` | Tkinter desktop interface |
| `scanner.py` | Exact-content matching and CSV export |
| `recycle.py` | Windows Recycle Bin integration |
| `Start Duplicate Finder.cmd` | Windows launcher |
| `tests/test_duplicate_finder.py` | Standard-library test suite |
| `INSTRUCTIONS.txt` | Detailed user guide |
