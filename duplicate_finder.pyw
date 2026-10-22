"""Duplicate Finder for Windows and macOS. Standalone releases bundle the runtime."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from scanner import Scanner, ScanResult, DuplicateGroup, export_csv, export_issues
from recycle import recycle_file
from platform_support import FILE_MANAGER, TRASH_NAME, TITLE_FONT, UI_FONT, reveal_file


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.2f} {unit}"
        size /= 1024


MINIMUMS = {"1 byte": 1, "1 KB": 1024, "1 MB": 1024**2, "10 MB": 10 * 1024**2,
            "100 MB": 100 * 1024**2, "Include empty files": 0}


class App:
    def __init__(self, root):
        self.root = root
        root.title("Duplicate Finder")
        root.geometry("1180x820")
        root.minsize(980, 700)
        root.configure(bg="#f1f4f6")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.result = None
        self.marked = set()
        self.rows = {}
        self.file_rows = {}
        self.group_ids = {}
        self.visible_paths = set()
        self.view = tk.StringVar(value="Matching content")
        self.filter = tk.StringVar()
        self.minimum = tk.StringVar(value="1 byte")
        self.skip_common = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Add one or more folders, then start a scan.")
        self.detail = tk.StringVar(value="Choose a file to see its full path. Click its Delete? box to mark it.")
        self.selection_text = tk.StringVar(value="No files selected for deletion")
        self.metric_values = [tk.StringVar(value="—") for _ in range(4)]
        self._filter_timer = None
        self._style()
        self._build()
        self.root.after(100, self.poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(UI_FONT, 10), background="#f1f4f6", foreground="#1b303e")
        style.configure("TButton", padding=(12, 6), background="#ffffff", borderwidth=1)
        style.map("TButton", background=[("active", "#e5edf0")])
        style.configure("Primary.TButton", background="#087e82", foreground="white", font=(TITLE_FONT, 10))
        style.map("Primary.TButton", background=[("disabled", "#b8cbd0"), ("active", "#056569")])
        style.configure("Danger.TButton", background="#a63838", foreground="white")
        style.map("Danger.TButton", background=[("disabled", "#cbd1d5"), ("active", "#852c2c")])
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=29, borderwidth=0)
        style.configure("Treeview.Heading", background="#e5ecef", font=(TITLE_FONT, 10), padding=6)
        style.map("Treeview", background=[("selected", "#d4ebed")], foreground=[("selected", "#12333e")])
        style.configure("TProgressbar", background="#087e82", troughcolor="#dfe8ec", thickness=5)

    def _build(self):
        header = tk.Frame(self.root, bg="#102f40", padx=24, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="Duplicate Finder", bg="#102f40", fg="white", font=(TITLE_FONT, 24)).pack(anchor="w")
        tk.Label(header, text="Find identical files. Review every copy. Choose what goes.", bg="#102f40", fg="#c0d9e3",
                 font=(UI_FONT, 11)).pack(anchor="w", pady=(3, 0))
        main = ttk.Frame(self.root, padding=(20, 12))
        main.pack(fill="both", expand=True)

        folders = ttk.Frame(main)
        folders.pack(fill="x")
        ttk.Label(folders, text="1   CHOOSE FOLDERS", font=(TITLE_FONT, 10)).pack(anchor="w", pady=(0, 5))
        folder_row = ttk.Frame(folders)
        folder_row.pack(fill="x")
        list_frame = ttk.Frame(folder_row)
        list_frame.pack(side="left", fill="both", expand=True)
        self.folder_list = tk.Listbox(list_frame, height=3, bg="white", fg="#1b303e", relief="flat", borderwidth=1,
                                      selectmode="extended", font=(UI_FONT, 10), exportselection=False)
        folder_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=folder_scroll.set)
        self.folder_list.pack(side="left", fill="both", expand=True)
        folder_scroll.pack(side="right", fill="y")
        buttons = ttk.Frame(folder_row, padding=(10, 0, 0, 0))
        buttons.pack(side="left", fill="y")
        self.add_button = ttk.Button(buttons, text="Add folder…", command=self.add_folder)
        self.add_button.pack(fill="x")
        self.remove_button = ttk.Button(buttons, text="Remove folder", command=self.remove_folder)
        self.remove_button.pack(fill="x", pady=(5, 0))
        opts = ttk.Frame(main)
        opts.pack(fill="x", pady=(9, 8))
        ttk.Label(opts, text="Minimum size").pack(side="left")
        self.minimum_box = ttk.Combobox(opts, textvariable=self.minimum, values=list(MINIMUMS), state="readonly", width=19)
        self.minimum_box.pack(side="left", padx=(7, 18))
        self.skip_check = ttk.Checkbutton(opts, text="Skip system and development folders", variable=self.skip_common)
        self.skip_check.pack(side="left")
        ttk.Button(opts, text="?", width=3, command=self.explain).pack(side="left", padx=5)
        self.stop_button = ttk.Button(opts, text="Stop", command=self.cancel.set, state="disabled")
        self.stop_button.pack(side="right")
        self.scan_button = ttk.Button(opts, text="Scan folders", command=self.scan, style="Primary.TButton")
        self.scan_button.pack(side="right", padx=(10, 7))

        cards = ttk.Frame(main)
        cards.pack(fill="x", pady=(0, 10))
        labels = ("FILES FOUND", "DUPLICATE MATCHES", "EXTRA COPIES", "EXTRA LOGICAL SIZE")
        for i, label in enumerate(labels):
            card = tk.Frame(cards, bg="white", padx=14, pady=8)
            card.grid(row=0, column=i, sticky="ew", padx=(0, 8 if i < 3 else 0))
            cards.columnconfigure(i, weight=1)
            tk.Label(card, text=label, bg="white", fg="#647985", font=(TITLE_FONT, 8)).pack(anchor="w")
            tk.Label(card, textvariable=self.metric_values[i], bg="white", fg="#087e82", font=(TITLE_FONT, 21)).pack(anchor="w")

        tools = ttk.Frame(main)
        tools.pack(fill="x", pady=(0, 6))
        ttk.Label(tools, text="2   REVIEW", font=(TITLE_FONT, 10)).pack(side="left", padx=(0, 14))
        ttk.Label(tools, text="Group by").pack(side="left")
        self.view_box = ttk.Combobox(tools, textvariable=self.view, values=["Matching content", "Folder"], state="readonly", width=18)
        self.view_box.pack(side="left", padx=(5, 14))
        self.view_box.bind("<<ComboboxSelected>>", lambda _: self.render())
        ttk.Label(tools, text="Filter path").pack(side="left")
        self.filter_entry = ttk.Entry(tools, textvariable=self.filter, width=26)
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.filter.trace_add("write", self.filter_later)
        self.export_button = ttk.Button(tools, text="Export CSV…", command=self.export, state="disabled")
        self.export_button.pack(side="right")

        table_frame = ttk.Frame(main)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table_frame, columns=("mark", "match", "size", "folder"), selectmode="extended")
        self.tree.heading("#0", text="Name / group")
        self.tree.heading("mark", text="Delete?")
        self.tree.heading("match", text="Match")
        self.tree.heading("size", text="Size / file")
        self.tree.heading("folder", text="Folder")
        for col, width, stretch in (("#0", 280, True), ("mark", 70, False), ("match", 80, False),
                                    ("size", 100, False), ("folder", 380, True)):
            self.tree.column(col, width=width, minwidth=width if not stretch else 160, stretch=stretch,
                             anchor="center" if col in ("mark", "match") else "w")
        self.tree.tag_configure("group", background="#eef4f6", font=(TITLE_FONT, 10))
        self.tree.tag_configure("marked", foreground="#9f3030")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Button-1>", self.click_box)
        self.tree.bind("<space>", self.toggle_highlighted)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)

        ttk.Label(main, textvariable=self.detail, wraplength=1090, foreground="#526b79").pack(fill="x", pady=(5, 5))
        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self.select_buttons = []
        for text, command in (("Select all visible", self.select_all), ("Select none", self.select_none),
                              ("Toggle highlighted", self.toggle_highlighted), (f"Reveal in {FILE_MANAGER}", self.reveal)):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            self.select_buttons.append(button)
        self.notes_button = ttk.Button(actions, text="Scan notes", command=self.notes, state="disabled")
        self.notes_button.pack(side="right")
        ttk.Button(actions, text="Copy paths", command=self.copy_paths).pack(side="right", padx=(0, 6))
        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(10, 6))
        ttk.Label(bottom, textvariable=self.selection_text, font=(TITLE_FONT, 10)).pack(side="left")
        self.delete_button = ttk.Button(bottom, text="Delete selected…", command=self.confirm_delete,
                                        style="Danger.TButton", state="disabled")
        self.delete_button.pack(side="right")
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill="x", pady=(2, 5))
        ttk.Label(main, textvariable=self.status, wraplength=1090, foreground="#526b79").pack(fill="x")

    def add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder to scan", mustexist=True)
        existing = {os.path.normcase(p) for p in self.folder_list.get(0, "end")}
        if folder and os.path.normcase(os.path.normpath(folder)) not in existing:
            self.folder_list.insert("end", os.path.normpath(folder))

    def remove_folder(self):
        for index in reversed(self.folder_list.curselection()):
            self.folder_list.delete(index)

    def explain(self):
        messagebox.showinfo("How matching works", "Files are grouped by size, sampled, hashed with SHA-256, then compared byte for byte. "
            "Different names can match; the same name alone is not a match.\n\n"
            "By default, Windows, Program Files, Program Files (x86), ProgramData, AppData, $Recycle.Bin, "
            "System Volume Information, .git, node_modules, .venv, venv and __pycache__ subfolders are skipped. "
            "An explicitly selected folder is still scanned.\n\n"
            "Links and junctions are not followed. Hard links are counted once. Cloud-only files are skipped. "
            "Empty files are skipped unless you include them.\n\n"
            "Extra logical size counts bytes in extra copies, not guaranteed free disk space. "
            "Recycled files still occupy space until Trash / Recycle Bin is emptied. "
            "Deleting synced files may also remove them from other synced devices.", parent=self.root)

    def set_busy(self, busy):
        self.busy = busy
        for button in [self.add_button, self.remove_button, self.scan_button, self.skip_check, *self.select_buttons]:
            button.configure(state="disabled" if busy else "normal")
        self.minimum_box.configure(state="disabled" if busy else "readonly")
        self.view_box.configure(state="disabled" if busy else "readonly")
        self.filter_entry.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.export_button.configure(state="normal" if not busy and self.result and self.result.groups else "disabled")
        self.notes_button.configure(state="normal" if not busy and self.result else "disabled")
        self.update_selection()

    def scan(self):
        roots = list(self.folder_list.get(0, "end"))
        if not roots:
            messagebox.showinfo("Choose folders", "Add at least one folder to scan.", parent=self.root)
            return
        self.cancel.clear()
        self.marked.clear()
        self.result = None
        self.render()
        self.set_busy(True)
        self.status.set("Starting scan…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(16)
        minimum, skip = MINIMUMS[self.minimum.get()], self.skip_common.get()
        def worker():
            last = [0.0]
            def progress(event):
                now = time.monotonic()
                if now - last[0] >= 0.12:
                    self.events.put(("progress", event))
                    last[0] = now
            try:
                result = Scanner(roots, minimum, skip, self.cancel, progress).run()
                self.events.put(("result", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    self.metric_values[0].set(f"{data['files']:,}")
                    if data["total"]:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=data["total"], value=data["current"])
                    short = data["path"] if len(data["path"]) < 110 else "…" + data["path"][-109:]
                    self.status.set(f"{data['phase']} · {data['files']:,} files found · {short}")
                elif kind == "result":
                    self.progress.stop()
                    self.result = data
                    self.progress.configure(mode="determinate", maximum=100, value=0 if data.cancelled else 100)
                    self.set_busy(False)
                    self.render()
                    if data.cancelled:
                        self.status.set("Scan stopped. Start a new scan to get complete results.")
                    else:
                        self.status.set(f"Scan complete · {data.eligible_files:,} eligible files · "
                            f"{data.folders_skipped:,} folders skipped · {data.cloud_skipped:,} cloud-only files skipped · "
                            f"{len(data.issues):,} issues. Review Scan notes for coverage.")
                elif kind == "delete_progress":
                    self.progress.configure(mode="determinate", maximum=data[1], value=data[0])
                    self.status.set(f"Rechecking and recycling {data[0] + 1} of {data[1]} · {Path(data[2]).name}")
                elif kind == "deleted":
                    removed, errors = data
                    kept = []
                    for group in self.result.groups:
                        files = tuple(r for r in group.files if r.path not in removed)
                        if len(files) > 1:
                            kept.append(DuplicateGroup(group.digest, group.size, files))
                    self.result.groups = kept
                    self.result.issues.extend(errors)
                    self.marked.clear()
                    self.progress.configure(maximum=100, value=100)
                    self.set_busy(False)
                    self.render()
                    self.status.set(f"Recycled {len(removed):,} files · {len(errors):,} skipped or failed. "
                                    "Results updated; scan again to refresh all files.")
                    if errors:
                        messagebox.showwarning("Some files were not recycled", f"{len(removed)} recycled; {len(errors)} skipped or failed.\n\n"
                            + "\n".join(f"{Path(p).name}: {e}" for p, e in errors[:5])
                            + "\n\nSee Scan notes for details.", parent=self.root)
                elif kind == "error":
                    self.progress.stop()
                    self.set_busy(False)
                    self.status.set("Operation failed. You can start another scan.")
                    messagebox.showerror("Operation failed", data, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def filter_later(self, *_):
        if self._filter_timer:
            self.root.after_cancel(self._filter_timer)
        self._filter_timer = self.root.after(200, self.render)

    def render(self):
        self._filter_timer = None
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.file_rows.clear()
        self.visible_paths.clear()
        self.group_ids.clear()
        groups = self.result.groups if self.result else []
        self.metric_values[0].set(f"{self.result.files_seen:,}" if self.result else "—")
        self.metric_values[1].set(f"{len(groups):,}" if self.result else "—")
        self.metric_values[2].set(f"{self.result.extra_files:,}" if self.result else "—")
        self.metric_values[3].set(human(self.result.extra_bytes) if self.result else "—")
        query = self.filter.get().strip().casefold()
        entries = []
        for number, group in enumerate(groups, 1):
            self.group_ids[id(group)] = number
            if self.view.get() == "Matching content":
                # Show every copy of a matching group so the filter cannot hide its siblings.
                if query and not any(query in r.path.casefold() for r in group.files):
                    continue
                entries.append((f"Match {number:03d} · {Path(group.files[0].path).name}",
                                [(r, group) for r in group.files], f"{len(group.files)} copies", human(group.size),
                                f"Extra copies: {human(group.extra_bytes)}"))
            else:
                for record in group.files:
                    if not query or query in record.path.casefold():
                        entries.append((str(Path(record.path).parent), [(record, group)], "", "", ""))
        if self.view.get() == "Folder":
            by_folder = defaultdict(list)
            for folder, files, *_ in entries:
                by_folder[folder].extend(files)
            entries = [(folder, sorted(files, key=lambda x: x[0].path.casefold()), f"{len(files)} files", "", "")
                       for folder, files in sorted(by_folder.items(), key=lambda x: x[0].casefold())]
        for label, files, match, size, folder in entries:
            parent = self.tree.insert("", "end", text=label, values=("[ ]", match, size, folder), open=True, tags=("group",))
            self.rows[parent] = [r.path for r, _ in files]
            for record, group in files:
                row = self.tree.insert(parent, "end", text=Path(record.path).name,
                    values=("[ ]", f"{self.group_ids[id(group)]:03d}", human(record.size), str(Path(record.path).parent)))
                self.rows[row] = [record.path]
                self.file_rows[row] = (record, group)
                self.visible_paths.add(record.path)
        self.refresh_marks()

    def refresh_marks(self):
        for row, paths in self.rows.items():
            count = sum(p in self.marked for p in paths)
            self.tree.set(row, "mark", "[x]" if count == len(paths) else "[–]" if count else "[ ]")
            if row in self.file_rows:
                self.tree.item(row, tags=("marked",) if count else ())
        self.update_selection()

    def records(self):
        return {r.path: (r, group) for group in (self.result.groups if self.result else []) for r in group.files}

    def update_selection(self):
        records = self.records()
        self.marked.intersection_update(records)
        hidden = len(self.marked - self.visible_paths)
        size = sum(records[p][0].size for p in self.marked)
        self.selection_text.set(f"{len(self.marked):,} files selected · {human(size)}" + (f" · {hidden:,} hidden by filter" if hidden else "")
                                if self.marked else "No files selected for deletion")
        self.delete_button.configure(state="normal" if self.marked and not self.busy else "disabled")

    def click_box(self, event):
        if self.busy or self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if row in self.rows:
            self.toggle_paths(self.rows[row])
            return "break"

    def toggle_paths(self, paths):
        paths = set(paths)
        if paths and paths <= self.marked:
            self.marked.difference_update(paths)
        else:
            self.marked.update(paths)
        self.refresh_marks()

    def toggle_highlighted(self, *_):
        if not self.busy:
            self.toggle_paths(p for row in self.tree.selection() for p in self.rows.get(row, []))
        return "break"

    def select_all(self):
        if not self.busy:
            self.marked.update(self.visible_paths)
            self.refresh_marks()

    def select_none(self):
        if not self.busy:
            self.marked.clear()
            self.refresh_marks()

    def show_detail(self, *_):
        rows = self.tree.selection()
        if rows and rows[-1] in self.file_rows:
            record, group = self.file_rows[rows[-1]]
            self.detail.set(f"{record.path}\nMatch {self.group_ids[id(group)]:03d} · {len(group.files)} identical copies · "
                            f"Modified {time.strftime('%Y-%m-%d %H:%M', time.localtime(record.signature[1] / 1e9))}")
        else:
            self.detail.set("Click Delete? boxes or highlight rows and press Space. Group boxes select the files inside that group.")

    def copy_paths(self):
        paths = sorted({p for row in self.tree.selection() for p in self.rows.get(row, [])})
        if paths:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(paths))
            self.status.set(f"Copied {len(paths)} highlighted paths.")

    def reveal(self):
        rows = self.tree.selection()
        if not rows:
            return
        row = rows[-1]
        paths = self.rows.get(row, [])
        if paths:
            path = paths[0]
            if not os.path.exists(path):
                messagebox.showinfo("File unavailable", "This file no longer exists. Scan again to refresh.", parent=self.root)
                return
            reveal_file(path)

    def export(self):
        if not self.result:
            return
        path = filedialog.asksaveasfilename(title="Export all duplicate matches", defaultextension=".csv",
                    initialfile="duplicate-files.csv", filetypes=[("CSV spreadsheet", "*.csv")])
        if path:
            try:
                export_csv(self.result, path)
                self.status.set(f"Exported all {len(self.result.groups):,} matches to {path}")
            except OSError as exc:
                messagebox.showerror("Export failed", str(exc), parent=self.root)

    def notes(self):
        if not self.result:
            return
        result = self.result
        window = tk.Toplevel(self.root)
        window.title("Scan notes & coverage")
        window.geometry("850x520")
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=(UI_FONT, 10), padx=10, pady=10)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        summary = (f"Folders selected:\n" + "\n".join(result.roots) + f"\n\nFiles found: {result.files_seen:,}\n"
            f"Eligible files: {result.eligible_files:,} ({human(result.eligible_bytes)})\n"
            f"Folders skipped by exclusions or junctions: {result.folders_skipped:,}\n"
            f"Cloud-only files skipped: {result.cloud_skipped:,}\n"
            f"Files below minimum size: {result.small_skipped:,}\n"
            f"Hard links / symbolic links skipped: {result.aliases_skipped:,}\n"
            f"Cancelled: {'Yes' if result.cancelled else 'No'}\n\n"
            "Results are a scan-time snapshot. Re-scan after outside changes. Extra logical size is not guaranteed recoverable disk space.\n\n"
            f"Issues ({len(result.issues):,}):\n")
        text.insert("end", summary)
        for path, reason in result.issues[:2000]:
            text.insert("end", f"{path}\n  {reason}\n\n")
        if len(result.issues) > 2000:
            text.insert("end", "Only the first 2,000 issues are shown. Export for the complete list.\n")
        text.configure(state="disabled")
        def save_notes():
            path = filedialog.asksaveasfilename(parent=window, defaultextension=".csv", initialfile="scan-issues.csv")
            if path:
                try:
                    export_issues(result, path)
                except OSError as exc:
                    messagebox.showerror("Export failed", str(exc), parent=window)
        ttk.Button(frame, text="Export all issues…", command=save_notes).pack(anchor="w", pady=(8, 0))

    def confirm_delete(self):
        if self.busy or not self.marked:
            return
        records = self.records()
        selected = sorted(self.marked)
        all_copy_groups = [g for g in self.result.groups if all(r.path in self.marked for r in g.files)]
        dialog = tk.Toplevel(self.root)
        dialog.title("Review selected files")
        dialog.geometry("880x580")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        total = sum(records[p][0].size for p in selected)
        ttk.Label(frame, text=f"Delete {len(selected):,} selected files?", font=(TITLE_FONT, 18)).pack(anchor="w")
        ttk.Label(frame, text=f"{human(total)} · Files will be sent to {TRASH_NAME}.", wraplength=820).pack(anchor="w", pady=(6, 10))
        if all_copy_groups:
            ttk.Label(frame, text=f"ALL COPIES SELECTED in {len(all_copy_groups)} duplicate matches. "
                "No scanned copy of those files will remain in its folder.", foreground="#a63838", wraplength=820,
                font=(TITLE_FONT, 10)).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="Review every path below. This list includes selections hidden by the current filter.", wraplength=820).pack(anchor="w")
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=8)
        listing = tk.Text(list_frame, wrap="none", height=12, font=(UI_FONT, 10), padx=8, pady=8)
        scroll = ttk.Scrollbar(list_frame, command=listing.yview)
        listing.configure(yscrollcommand=scroll.set)
        listing.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=listing.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        listing.configure(xscrollcommand=horizontal.set)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        listing.insert("end", "\n".join(selected))
        listing.configure(state="disabled")
        ttk.Label(frame, text="Synced-folder deletions may sync to your other devices. Recycled files occupy space until "
            "Trash / Recycle Bin is emptied. Files that changed or cannot be recycled will be skipped.", wraplength=820).pack(anchor="w", pady=5)
        understood = tk.BooleanVar(value=not all_copy_groups)
        actions = ttk.Frame(frame)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        proceed = ttk.Button(actions, text=f"Move to {TRASH_NAME}", style="Danger.TButton",
                             state="normal" if understood.get() else "disabled")
        def begin():
            dialog.destroy()
            self.delete_files([records[p] for p in selected])
        proceed.configure(command=begin)
        proceed.pack(side="right")
        cancel_button = ttk.Button(actions, text="Cancel", command=dialog.destroy)
        cancel_button.pack(side="right", padx=8)
        if all_copy_groups:
            ttk.Checkbutton(frame, text="I understand that every scanned copy in these matches is selected.",
                variable=understood, command=lambda: proceed.configure(state="normal" if understood.get() else "disabled")).pack(anchor="w", pady=5)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        cancel_button.focus_set()
        return dialog

    def delete_files(self, selected):
        self.cancel.clear()
        self.set_busy(True)
        self.progress.configure(maximum=len(selected), value=0)
        def worker():
            removed, errors = set(), []
            verifier = Scanner([])
            for index, (record, group) in enumerate(selected):
                if self.cancel.is_set():
                    errors.extend((r.path, "Stopped before recycling") for r, _ in selected[index:])
                    break
                self.events.put(("delete_progress", (index, len(selected), record.path)))
                try:
                    if verifier.hash_file(record) != group.digest:
                        raise OSError("File changed or is unavailable. Scan again.")
                    # Check cancellation after hashing a large file and before mutation.
                    if self.cancel.is_set():
                        raise OSError("Stopped before recycling")
                    recycle_file(record.path, lambda r=record: verifier.unchanged(r))
                    removed.add(record.path)
                except Exception as exc:
                    errors.append((record.path, str(exc)))
            self.events.put(("deleted", (removed, errors)))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Press Stop, wait for the current file to finish, then close the app.", parent=self.root)
            return
        self.root.destroy()


def main():
    if "--self-test" in sys.argv:
        from frozen_smoke import run_self_test
        run_self_test(App, sys.argv[sys.argv.index("--self-test") + 1])
        return
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except OSError:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
