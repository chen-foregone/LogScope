"""本机Windows内核身份观察；不支持或不可访问时保守返回unknown。"""
import os
import sys


def _machine():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                        access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return winreg.QueryValueEx(key, 'MachineGuid')[0]


def _observe(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return {'state': 'exited' if ctypes.get_last_error() == 87 else 'unknown'}
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return {'state': 'unknown'}
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        waited = kernel.WaitForSingleObject(handle, 0)
        return {'created': str(created), 'state': 'exited' if waited == 0 else 'alive' if waited == 258 else 'unknown'}
    finally:
        kernel.CloseHandle(handle)


def current_process():
    if sys.platform != 'win32':
        return None
    try:
        observed = _observe(os.getpid())
        if observed['state'] != 'alive':
            return None
        return {'platform': 'windows_kernel_v1', 'machine': _machine(),
                'pid': os.getpid(), 'created': observed['created']}
    except (OSError, ValueError):
        return None


def observe_process(identity):
    if sys.platform != 'win32' or not identity or identity.get('platform') != 'windows_kernel_v1':
        return {'state': 'unknown'}
    try:
        if identity['machine'] != _machine():
            return {'state': 'unknown'}
        observed = _observe(identity['pid'])
        if observed.get('created') and observed['created'] != identity['created']:
            return {'state': 'exited', 'proof': 'pid_reused_creation_changed', 'pid': identity['pid'], 'created': identity['created']}
        return {'state': observed['state'], 'proof': 'windows_kernel_process_observation',
                'pid': identity['pid'], 'created': identity['created']}
    except (OSError, ValueError, KeyError):
        return {'state': 'unknown'}
