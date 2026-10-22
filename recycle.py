"""Windows Recycle Bin via IFileOperation. No permanent-delete fallback.

Reference: Microsoft IFileOperation / IFileOperationProgressSink documentation.
PreDeleteItem rejects operations without TSF_DELETE_RECYCLE_IF_POSSIBLE.
"""
from __future__ import annotations

import ctypes as C
import os
import uuid


def recycle_file(path: str, still_valid=lambda: True):
    if os.name != "nt":
        raise OSError("Recycling is supported on Windows only")
    from ctypes import wintypes as W

    HRESULT = C.c_long
    PTR = C.c_void_p
    CALLBACK = C.WINFUNCTYPE

    class GUID(C.Structure):
        _fields_ = [("data", C.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__()
            C.memmove(C.addressof(self), uuid.UUID(value).bytes_le, 16)

    iid_unknown = GUID("00000000-0000-0000-C000-000000000046")
    iid_sink = GUID("04b0f1a7-9490-44bc-96e1-4296a31252e2")
    iid_operation = GUID("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
    cls_operation = GUID("3ad05575-8857-4850-9277-11b85bdb8e09")
    iid_item = GUID("43826d1e-e718-42ee-bc55-a1e261c37bfe")

    def check(hr):
        if hr < 0:
            raise OSError(f"Windows could not recycle this file (0x{hr & 0xffffffff:08X})")

    def method(obj, index, *types):
        table = C.cast(obj, C.POINTER(C.POINTER(PTR))).contents
        return CALLBACK(HRESULT, PTR, *types)(table[index])

    state = {"refs": 1, "blocked": False, "changed": False, "completed": False, "error": None}
    callbacks = []

    def query(this, iid, out):
        requested = C.string_at(iid, 16)
        if requested in (bytes(iid_unknown.data), bytes(iid_sink.data)):
            out[0] = this
            state["refs"] += 1
            return 0
        out[0] = None
        return -2147467262  # E_NOINTERFACE

    def addref(this):
        state["refs"] += 1
        return state["refs"]

    def release(this):
        state["refs"] -= 1
        return state["refs"]

    def pre_delete(this, flags, item):
        try:
            if not still_valid():
                state["changed"] = True
                return -2147467260  # E_ABORT
            if not flags & 0x80:
                state["blocked"] = True
                return -2147467260
            return 0
        except Exception:
            state["changed"] = True
            return -2147467260

    def post_delete(this, flags, item, hr, new_item):
        state["error"] = hr if hr < 0 else None
        state["completed"] = hr >= 0 and bool(new_item)
        return 0

    def callback(result, types, fn):
        cb = CALLBACK(result, PTR, *types)(fn)
        callbacks.append(cb)
        return C.cast(cb, PTR).value

    def noop(*args):
        return 0

    # Exact IFileOperationProgressSink vtable, including IUnknown.
    entries = [
        callback(HRESULT, [PTR, C.POINTER(PTR)], query),
        callback(W.ULONG, [], addref), callback(W.ULONG, [], release),
        callback(HRESULT, [], noop),                         # StartOperations
        callback(HRESULT, [HRESULT], noop),                # FinishOperations
        callback(HRESULT, [W.DWORD, PTR, W.LPCWSTR], noop),  # PreRenameItem
        callback(HRESULT, [W.DWORD, PTR, W.LPCWSTR, HRESULT, PTR], noop),
        callback(HRESULT, [W.DWORD, PTR, PTR, W.LPCWSTR], noop),  # PreMoveItem
        callback(HRESULT, [W.DWORD, PTR, PTR, W.LPCWSTR, HRESULT, PTR], noop),
        callback(HRESULT, [W.DWORD, PTR, PTR, W.LPCWSTR], noop),  # PreCopyItem
        callback(HRESULT, [W.DWORD, PTR, PTR, W.LPCWSTR, HRESULT, PTR], noop),
        callback(HRESULT, [W.DWORD, PTR], pre_delete),
        callback(HRESULT, [W.DWORD, PTR, HRESULT, PTR], post_delete),
        callback(HRESULT, [W.DWORD, PTR, W.LPCWSTR], noop),  # PreNewItem
        callback(HRESULT, [W.DWORD, PTR, W.LPCWSTR, W.LPCWSTR, W.DWORD, HRESULT, PTR], noop),
        callback(HRESULT, [W.UINT, W.UINT], noop),          # UpdateProgress
        callback(HRESULT, [], noop), callback(HRESULT, [], noop), callback(HRESULT, [], noop),
    ]
    table = (PTR * len(entries))(*entries)

    class Sink(C.Structure):
        _fields_ = [("vtable", C.POINTER(PTR))]

    sink = Sink(table)
    ole = C.OleDLL("ole32")
    shell = C.WinDLL("shell32")
    ole.CoInitializeEx.argtypes = [PTR, W.DWORD]
    ole.CoInitializeEx.restype = HRESULT
    ole.CoCreateInstance.argtypes = [C.POINTER(GUID), PTR, W.DWORD, C.POINTER(GUID), C.POINTER(PTR)]
    ole.CoCreateInstance.restype = HRESULT
    shell.SHCreateItemFromParsingName.argtypes = [W.LPCWSTR, PTR, C.POINTER(GUID), C.POINTER(PTR)]
    shell.SHCreateItemFromParsingName.restype = HRESULT
    check(ole.CoInitializeEx(None, 2))  # STA; each worker owns its COM initialization.
    operation, item = PTR(), PTR()
    try:
        check(ole.CoCreateInstance(C.byref(cls_operation), None, 1, C.byref(iid_operation), C.byref(operation)))
        check(shell.SHCreateItemFromParsingName(os.path.abspath(path), None, C.byref(iid_item), C.byref(item)))
        # Silent, confirmed by our own review dialog; fail early; recycle; undo.
        flags = 0x0004 | 0x0010 | 0x0400 | 0x00100000 | 0x00080000 | 0x20000000
        check(method(operation, 5, W.DWORD)(operation, flags))
        check(method(operation, 18, PTR, PTR)(operation, item, C.byref(sink)))
        hr = method(operation, 21)(operation)
        aborted = W.BOOL()
        check(method(operation, 22, C.POINTER(W.BOOL))(operation, C.byref(aborted)))
        if state["blocked"]:
            raise OSError("Windows cannot recycle this file. Permanent deletion was blocked.")
        if state["changed"]:
            raise OSError("File changed since verification; skipped. Scan again.")
        check(hr)
        if state["error"] is not None:
            check(state["error"])
        if aborted.value or not state["completed"]:
            raise OSError("Recycling was cancelled or not confirmed by Windows")
    finally:
        if item:
            method(item, 2)(item)
        if operation:
            method(operation, 2)(operation)
        ole.CoUninitialize()
