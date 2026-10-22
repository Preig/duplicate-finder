"""Disposable-fixture checks for bundled applications; never accepts scan roots."""
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import tkinter as tk

from recycle import recycle_file
from scanner import Scanner


def run_self_test(app_class, output):
    report = {"platform": sys.platform, "architecture": platform.machine(),
              "bundled_runtime": bool(getattr(sys, "frozen", False)), "status": "failed"}
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = app_class(root)
        temp_root = None
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            candidate = Path(os.environ["LOCALAPPDATA"]) / "Temp"
            if candidate.is_dir():
                temp_root = candidate
        with tempfile.TemporaryDirectory(prefix="duplicate-finder-selftest-", dir=temp_root) as tmp:
            folder = Path(tmp)
            for relative in ["first/a.txt", "second/b.txt", "second/c.txt"]:
                path = folder / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"Disposable Duplicate Finder bundled-runtime fixture.\n")
            (folder / "unique.txt").write_bytes(b"Unique file, deliberately different length.")
            result = Scanner([str(folder)]).run()
            assert len(result.groups) == 1 and len(result.groups[0].files) == 3
            assert not result.issues
            app.result = result
            app.render()
            assert not app.marked
            app.view.set("Folder")
            app.render()
            assert len(app.tree.get_children()) == 2
            app.select_all()
            assert len(app.marked) == 3
            app.select_none()
            assert not app.marked
            record = result.groups[0].files[0]
            verifier = Scanner([])
            assert verifier.hash_file(record) == result.groups[0].digest
            recycle_file(record.path, lambda: verifier.unchanged(record))
            assert not Path(record.path).exists()
            assert all(Path(r.path).exists() for r in result.groups[0].files[1:])
            report.update(status="passed", tkinter="passed", exact_matching="passed",
                          folder_grouping="passed", selection="passed", native_trash="passed")
    except Exception as exc:
        # Do not write machine-specific fixture paths into published reports.
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if root is not None:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
