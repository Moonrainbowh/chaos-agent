"""A bounded, windowless Windows ConPTY for real terminal input tests."""
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes as w


class _Coord(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), *[(name, w.DWORD) for name in
                ("x", "y", "width", "height", "cols", "rows", "fill", "flags")],
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", ctypes.c_void_p),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class _StartupEx(ctypes.Structure):
    _fields_ = [("info", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class ConsoleProcess:
    def __init__(self, program):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._signatures()
        self._handles = []
        self.hpc = w.HANDLE()
        self.process = _ProcessInfo()
        self.output = bytearray()
        self.changed = threading.Condition()
        self.reader = None
        try:
            self._start(program)
        except BaseException:
            self.close()
            raise

    def _signatures(self):
        k = self.k
        k.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD]
        k.CreatePseudoConsole.argtypes = [_Coord, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        k.CreatePseudoConsole.restype = ctypes.c_long
        k.ClosePseudoConsole.argtypes = [w.HANDLE]
        k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_ProcessInfo)]
        for name in ("ReadFile", "WriteFile"):
            getattr(k, name).argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        k.CloseHandle.argtypes = [w.HANDLE]

    def _pipe(self):
        read, write = w.HANDLE(), w.HANDLE()
        if not self.k.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handles.extend((read, write))
        return read, write

    def _start(self, program):
        incoming, self.input = self._pipe()
        self.read, outgoing = self._pipe()
        result = self.k.CreatePseudoConsole(_Coord(100, 30), incoming, outgoing, 0, ctypes.byref(self.hpc))
        if result:
            raise OSError(f"CreatePseudoConsole failed: {result}")
        size = ctypes.c_size_t()
        self.k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not self.k.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.k.UpdateProcThreadAttribute(attributes, 0, 0x00020016, self.hpc, ctypes.sizeof(w.HANDLE), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            startup = _StartupEx()
            startup.info.cb = ctypes.sizeof(startup)
            startup.attributes = ctypes.addressof(attributes)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([sys.executable, "-X", "utf8", "-c", program]))
            if not self.k.CreateProcessW(None, command, None, None, False, 0x00080000, None, None, ctypes.byref(startup), ctypes.byref(self.process)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.k.DeleteProcThreadAttributeList(attributes)
        self.k.CloseHandle(incoming)
        self.k.CloseHandle(outgoing)
        self._handles.remove(incoming)
        self._handles.remove(outgoing)
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        buffer, count = ctypes.create_string_buffer(4096), w.DWORD()
        while self.k.ReadFile(self.read, buffer, len(buffer), ctypes.byref(count), None) and count.value:
            with self.changed:
                self.output.extend(buffer.raw[:count.value])
                if len(self.output) > 1_000_000:
                    del self.output[:-1_000_000]
                self.changed.notify_all()

    def send(self, text):
        encoded, count = text.encode("utf-8"), w.DWORD()
        if not self.k.WriteFile(self.input, encoded, len(encoded), ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if count.value != len(encoded):
            raise OSError("partial terminal input write")

    def resize(self, width, height):
        self.k.ResizePseudoConsole.argtypes = [w.HANDLE, _Coord]
        self.k.ResizePseudoConsole.restype = ctypes.c_long
        result = self.k.ResizePseudoConsole(self.hpc, _Coord(width, height))
        if result:
            raise OSError(f"ResizePseudoConsole failed: {result}")

    def wait_for(self, marker, timeout=10):
        deadline = time.monotonic() + timeout
        with self.changed:
            while marker.encode() not in self.output:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"missing {marker}: {self.output.decode('utf-8', errors='replace')}")
                self.changed.wait(remaining)
            return self.output.decode("utf-8", errors="replace")

    def close(self):
        if self.process.process:
            if self.k.WaitForSingleObject(self.process.process, 1000) == 258:
                self.k.TerminateProcess(self.process.process, 1)
                self.k.WaitForSingleObject(self.process.process, 1000)
        if self.hpc:
            self.k.ClosePseudoConsole(self.hpc)
        if self.reader is not None:
            self.reader.join(2)
        for handle in self._handles + [self.process.process, self.process.thread]:
            if handle:
                self.k.CloseHandle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
