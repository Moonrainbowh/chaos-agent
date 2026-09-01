from __future__ import annotations

import ctypes
import os
from typing import Any


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_BOOL = ctypes.c_int32
_DWORD = ctypes.c_uint32
_HANDLE = ctypes.c_void_p
_LARGE_INTEGER = ctypes.c_int64
_SIZE_T = ctypes.c_size_t
_UINT = ctypes.c_uint32
_ULONG_PTR = ctypes.c_size_t
_ULONGLONG = ctypes.c_uint64


class _IoCounters(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", _ULONGLONG),
        ("WriteOperationCount", _ULONGLONG),
        ("OtherOperationCount", _ULONGLONG),
        ("ReadTransferCount", _ULONGLONG),
        ("WriteTransferCount", _ULONGLONG),
        ("OtherTransferCount", _ULONGLONG),
    )


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", _ULONG_PTR),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    )


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    )


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", _LARGE_INTEGER),
        ("TotalKernelTime", _LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", _LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", _LARGE_INTEGER),
        ("TotalPageFaultCount", _DWORD),
        ("TotalProcesses", _DWORD),
        ("ActiveProcesses", _DWORD),
        ("TotalTerminatedProcesses", _DWORD),
    )


class WindowsJobError(OSError):
    def __init__(self, operation: str, error_code: int) -> None:
        self.operation = operation
        self.error_code = error_code
        super().__init__(error_code, f"{operation} failed with Win32 error {error_code}")


class WindowsJob:
    """Own one unnamed kill-on-close Job Object and its root assignment."""

    def __init__(self, handle: int, api: Any) -> None:
        self._handle = handle
        self._process_handle = 0
        self._api = api
        self._assigned = False

    @classmethod
    def create(cls, api: Any | None = None) -> "WindowsJob":
        active_api = api or _Kernel32JobApi()
        handle = active_api.create_job()
        try:
            active_api.set_kill_on_close(handle)
        except BaseException as setup_error:
            try:
                active_api.close_handle(handle)
            except BaseException as close_error:
                raise close_error from setup_error
            raise
        return cls(handle, active_api)

    @property
    def handle(self) -> int:
        if self._handle == 0:
            raise WindowsJobError("use closed job", 6)
        return self._handle

    @property
    def assigned(self) -> bool:
        return self._assigned

    @property
    def closed(self) -> bool:
        return self._handle == 0 and self._process_handle == 0

    def assign(self, pid: int) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        if self._process_handle:
            raise WindowsJobError("assign with pending process handle", 170)
        self._process_handle = self._api.open_process(pid)
        try:
            self._api.assign_process(self.handle, self._process_handle)
            self._assigned = True
        finally:
            self._close_process_handle()

    def terminate(self, exit_code: int = 1) -> None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise TypeError("exit_code must be an integer")
        if not 0 <= exit_code <= 0xFFFFFFFF:
            raise ValueError("exit_code must fit an unsigned 32-bit integer")
        if self._assigned:
            self._api.terminate_job(self.handle, exit_code)

    def active_processes(self) -> int:
        return self._api.active_processes(self.handle)

    def close(self) -> None:
        if self.closed:
            return
        errors: list[BaseException] = []
        try:
            self._close_process_handle()
        except BaseException as error:
            errors.append(error)
        if self._handle:
            handle = self._handle
            try:
                self._api.close_handle(handle)
            except BaseException as error:
                errors.append(error)
            else:
                self._handle = 0
        if len(errors) > 1:
            raise errors[-1] from errors[0]
        if errors:
            raise errors[0]

    def _close_process_handle(self) -> None:
        if not self._process_handle:
            return
        handle = self._process_handle
        self._api.close_handle(handle)
        self._process_handle = 0

    def __enter__(self) -> "WindowsJob":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class _Kernel32JobApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsJobError("create job", 50)
        self._dll = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare_prototypes(self._dll)

    def create_job(self) -> int:
        handle = self._dll.CreateJobObjectW(None, None)
        if not handle:
            self._raise("CreateJobObjectW")
        return int(handle)

    def set_kill_on_close(self, handle: int) -> None:
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self._dll.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise("SetInformationJobObject")

    def open_process(self, pid: int) -> int:
        handle = self._dll.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid
        )
        if not handle:
            self._raise("OpenProcess")
        return int(handle)

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        if not self._dll.AssignProcessToJobObject(job_handle, process_handle):
            self._raise("AssignProcessToJobObject")

    def terminate_job(self, handle: int, exit_code: int) -> None:
        if not self._dll.TerminateJobObject(handle, exit_code):
            self._raise("TerminateJobObject")

    def active_processes(self, handle: int) -> int:
        information = _BasicAccountingInformation()
        if not self._dll.QueryInformationJobObject(
            handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            self._raise("QueryInformationJobObject")
        return int(information.ActiveProcesses)

    def close_handle(self, handle: int) -> None:
        if not self._dll.CloseHandle(handle):
            self._raise("CloseHandle")

    @staticmethod
    def _raise(operation: str) -> None:
        raise WindowsJobError(operation, ctypes.get_last_error())


def _declare_prototypes(dll: Any) -> None:
    dll.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    dll.CreateJobObjectW.restype = _HANDLE
    dll.SetInformationJobObject.argtypes = (
        _HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD
    )
    dll.SetInformationJobObject.restype = _BOOL
    dll.OpenProcess.argtypes = (_DWORD, _BOOL, _DWORD)
    dll.OpenProcess.restype = _HANDLE
    dll.AssignProcessToJobObject.argtypes = (_HANDLE, _HANDLE)
    dll.AssignProcessToJobObject.restype = _BOOL
    dll.TerminateJobObject.argtypes = (_HANDLE, _UINT)
    dll.TerminateJobObject.restype = _BOOL
    dll.QueryInformationJobObject.argtypes = (
        _HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD),
    )
    dll.QueryInformationJobObject.restype = _BOOL
    dll.CloseHandle.argtypes = (_HANDLE,)
    dll.CloseHandle.restype = _BOOL


def _validate_structure_layout() -> None:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    expected_basic = 64 if pointer_size == 8 else 48
    expected_extended = 144 if pointer_size == 8 else 112
    actual = (ctypes.sizeof(_BasicLimitInformation), ctypes.sizeof(_ExtendedLimitInformation))
    if actual != (expected_basic, expected_extended):
        raise RuntimeError(f"unexpected Windows Job Object structure layout: {actual}")


_validate_structure_layout()
