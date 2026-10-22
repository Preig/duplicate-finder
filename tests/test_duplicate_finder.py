import csv
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from scanner import Scanner, export_csv
from recycle import recycle_file
loader = importlib.machinery.SourceFileLoader('finder_app', str(APP_DIR / 'duplicate_finder.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
app_module = importlib.util.module_from_spec(spec)
loader.exec_module(app_module)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='duplicate-finder-test-', dir=os.environ.get('DUPLICATE_FINDER_TEST_ROOT', str(Path(__file__).parent)))
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class ScannerTests(Fixture):

    def test_matches_different_names_not_same_names_and_discriminates_tail(self):
        content = b'a' * 65536 + b'first'
        a = self.write('one/same.bin', content)
        b = self.write('two/different.bin', content)
        self.write('three/same.bin', b'a' * 65536 + b'other')
        self.write('unique.bin', b'unique size')
        result = Scanner([str(self.folder)]).run()
        self.assertEqual(len(result.groups), 1)
        self.assertEqual({r.path for r in result.groups[0].files}, {str(a), str(b)})
        self.assertEqual(result.extra_files, 1)
        self.assertEqual(result.extra_bytes, len(content))

    def test_overlapping_roots_hardlinks_and_empty_files(self):
        a = self.write('one/a.txt', b'hello')
        self.write('two/b.txt', b'hello')
        os.link(a, self.folder / 'hardlink.txt')
        self.write('one/empty', b'')
        self.write('two/empty', b'')
        result = Scanner([str(self.folder), str(self.folder / 'one')], minimum=0).run()
        self.assertEqual(sorted(len(g.files) for g in result.groups), [2, 2])
        self.assertEqual(result.aliases_skipped, 1)
        self.assertEqual(result.extra_bytes, 5)

    def test_exclusions_can_be_disabled_and_explicit_roots_work(self):
        self.write('a.txt', b'hello')
        self.write('node_modules/b.txt', b'hello')
        self.assertEqual(len(Scanner([str(self.folder)]).run().groups), 0)
        self.assertEqual(len(Scanner([str(self.folder)], skip_common=False).run().groups), 1)
        self.assertEqual(len(Scanner([str(self.folder), str(self.folder / 'node_modules')]).run().groups), 1)

    def test_minimum_and_cancel(self):
        self.write('a', b'a')
        self.write('b', b'a')
        result = Scanner([str(self.folder)], minimum=2).run()
        self.assertEqual(result.groups, [])
        self.assertEqual(result.small_skipped, 2)
        event = threading.Event()
        def progress(_):
            event.set()
        result = Scanner([str(self.folder)], cancel=event, progress=progress).run()
        self.assertTrue(result.cancelled)
        self.assertEqual(result.groups, [])

    def test_changed_file_rejected(self):
        self.write('a', b'hello')
        self.write('b', b'hello')
        changed = [False]
        def progress(event):
            if event['phase'] == 'Hashing complete files' and not changed[0]:
                path = Path(event['path'])
                before = path.stat()
                path.write_bytes(b'HELLO')
                # Make the concurrent change observable even on coarse clocks.
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
                changed[0] = True
        result = Scanner([str(self.folder)], progress=progress).run()
        self.assertTrue(changed[0])
        self.assertEqual(result.groups, [])
        self.assertTrue(result.issues)

    def test_byte_comparison_handles_mock_hash_collision(self):
        self.write('a', b'hello')
        self.write('b', b'world')
        with patch.object(Scanner, 'hash_file', return_value='collision'):
            self.assertEqual(Scanner([str(self.folder)]).run().groups, [])

    def test_unicode_and_csv_export(self):
        a = self.write('one/café_日本語.txt', b'hello')
        self.write('two/comma,file.txt', b'hello')
        result = Scanner([str(self.folder)]).run()
        target = self.folder / 'report.csv'
        export_csv(result, str(target))
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 3)
        self.assertIn(str(a), [r[5] for r in rows[1:]])

    def test_unavailable_root_reported(self):
        result = Scanner([str(self.folder / 'missing')]).run()
        self.assertEqual(len(result.issues), 1)


@unittest.skipUnless(sys.platform in ('win32', 'darwin'), 'Desktop integration tests')
class AppTests(Fixture):
    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app_module.App(self.root)
        self.write('one/a.txt', b'hello')
        self.write('two/b.txt', b'hello')
        self.write('two/c.txt', b'world')
        self.write('three/d.txt', b'world')
        self.app.result = Scanner([str(self.folder)]).run()
        self.app.render()
        self.root.update()

    def tearDown(self):
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()
        super().tearDown()

    def test_select_none_one_many_all_and_group_by_folder(self):
        app = self.app
        self.assertEqual(len(app.marked), 0)
        one = next(iter(app.visible_paths))
        app.toggle_paths([one])
        self.assertEqual(app.marked, {one})
        app.view.set('Folder')
        app.render()
        self.assertEqual(len(app.tree.get_children()), 3)
        self.assertEqual(app.marked, {one})
        app.select_all()
        self.assertEqual(len(app.marked), 4)
        app.toggle_paths([one])
        self.assertEqual(len(app.marked), 3)
        app.select_none()
        self.assertEqual(app.marked, set())

    def test_filter_keeps_selection_and_reports_hidden(self):
        app = self.app
        app.select_all()
        app.view.set('Folder')
        app.filter.set(str(self.folder / 'one'))
        app.render()
        self.assertEqual(len(app.visible_paths), 1)
        self.assertEqual(len(app.marked), 4)
        self.assertIn('3 hidden', app.selection_text.get())
        app.select_none()
        self.assertEqual(app.marked, set())

    def test_content_filter_preserves_sibling_copies(self):
        self.app.filter.set('a.txt')
        self.app.render()
        self.assertEqual(len(self.app.visible_paths), 2)

    def test_delete_dialog_requires_all_copy_acknowledgement(self):
        app = self.app
        app.select_all()
        dialog = app.confirm_delete()
        self.root.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(dialog))
        proceed = next(w for w in widgets if w.winfo_class() == 'TButton' and w.cget('text') == f'Move to {app_module.TRASH_NAME}')
        acknowledgement = next(w for w in widgets if w.winfo_class() == 'TCheckbutton')
        self.assertIn('disabled', proceed.state())
        acknowledgement.invoke()
        self.assertNotIn('disabled', proceed.state())
        dialog.destroy()
        self.assertTrue(all(Path(p).exists() for p in app.marked))

    def test_changed_selection_never_reaches_recycler(self):
        record, group = next(iter(self.app.records().values()))
        Path(record.path).write_bytes(b'CHANGED')
        with patch.object(app_module, 'recycle_file') as recycler:
            self.app.delete_files([(record, group)])
            deadline = time.monotonic() + 5
            while self.app.busy and time.monotonic() < deadline:
                # Consume only terminal event to avoid warning modals in this unit test.
                try:
                    kind, payload = self.app.events.get(timeout=.1)
                except Exception:
                    continue
                if kind == 'deleted':
                    self.assertEqual(payload[0], set())
                    self.assertEqual(len(payload[1]), 1)
                    self.app.busy = False
            recycler.assert_not_called()
        self.assertTrue(Path(record.path).exists())

    def test_worker_recycles_one_selected_file_and_preserves_other_copies(self):
        self.write('three/e.txt', b'hello')
        app = self.app
        app.result = Scanner([str(self.folder)]).run()
        app.render()
        group = next(g for g in app.result.groups if len(g.files) == 3)
        record = group.files[0]
        siblings = group.files[1:]
        app.delete_files([(record, group)])
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(app.busy)
        self.assertFalse(Path(record.path).exists())
        self.assertTrue(all(Path(r.path).exists() for r in siblings))
        self.assertIn('Recycled 1 files', app.status.get())
        self.assertEqual(len(app.result.groups), 2)

    def test_worker_can_recycle_all_selected_copies(self):
        app = self.app
        selected = list(app.records().values())
        app.delete_files(selected)
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(app.busy)
        self.assertTrue(all(not Path(r.path).exists() for r, _ in selected))
        self.assertEqual(app.result.groups, [])

    def test_layout_controls_fit_normal_and_minimum_window(self):
        self.root.deiconify()
        for width,height in [(1180,820),self.root.minsize()]:
            self.root.geometry(f'{width}x{height}+20+20')
            # Cocoa delivers resize/configure events asynchronously; let the
            # native window and its Tk children settle before checking bounds.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.root.update()
                widgets = [self.app.scan_button,self.app.tree,self.app.delete_button,self.app.progress]
                fits = all(w.winfo_rootx()-self.root.winfo_rootx()+w.winfo_width() <= self.root.winfo_width()
                           and w.winfo_rooty()-self.root.winfo_rooty()+w.winfo_height() <= self.root.winfo_height()
                           for w in widgets)
                if fits and self.app.tree.winfo_height() >= 100:
                    break
                time.sleep(.01)
            for widget in [self.app.scan_button,self.app.tree,self.app.delete_button,self.app.progress]:
                x=widget.winfo_rootx()-self.root.winfo_rootx()
                y=widget.winfo_rooty()-self.root.winfo_rooty()
                self.assertGreaterEqual(x,0)
                self.assertGreaterEqual(y,0)
                context = (f"widget={widget.winfo_class()} geometry={widget.winfo_geometry()}; "
                           f"window={self.root.winfo_geometry()}; minimum={self.root.minsize()}")
                self.assertLessEqual(x+widget.winfo_width(),self.root.winfo_width(), context)
                self.assertLessEqual(y+widget.winfo_height(),self.root.winfo_height(), context)
            self.assertGreaterEqual(self.app.tree.winfo_height(),100,
                f"window={width}x{height}; actual={self.root.winfo_geometry()}; "
                f"tree={self.app.tree.winfo_geometry()}; requested={self.root.winfo_reqheight()}; scaling={self.root.tk.call('tk','scaling')}")


@unittest.skipUnless(sys.platform in ('win32', 'darwin'), 'Native Trash integration tests')
class RecycleTests(Fixture):
    def test_only_disposable_fixture_is_recycled(self):
        target = self.write('RECYCLE-TEST-ONLY.txt', b'Temporary Duplicate Finder recycle test fixture.')
        recycle_file(str(target))
        self.assertFalse(target.exists())

    def test_last_moment_change_blocks_recycling(self):
        target = self.write('KEEP-TEST-ONLY.txt', b'This fixture must remain.')
        with self.assertRaisesRegex(OSError, 'changed'):
            recycle_file(str(target), lambda: False)
        self.assertTrue(target.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
