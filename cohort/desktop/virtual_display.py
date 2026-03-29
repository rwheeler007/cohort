"""Parsec Virtual Display Driver control via direct DeviceIoControl.

Talks to the Parsec VDD IddCx driver to create/remove virtual monitors
at exact pixel dimensions. Requires the Parsec VDD driver to be installed
(C:\\Program Files\\Parsec Virtual Display Driver).

Architecture:
    - SetupAPI opens a handle to the Parsec VDD adapter device
    - IOCTL calls add/remove virtual displays
    - A daemon thread sends keepalive pings every 100ms (required by driver)
    - Win32 ChangeDisplaySettingsEx sets the exact resolution
    - EnumDisplayDevices finds the Parsec display device name

Reference: https://github.com/nomi-san/parsec-vdd (parsec-vdd.h)
"""

import ctypes
import ctypes.wintypes as wt
import logging
import struct
import threading
import time
from ctypes import POINTER, Structure, byref, c_byte, c_ulong, c_wchar, sizeof, windll
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows API constants
# ---------------------------------------------------------------------------

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

ERROR_INSUFFICIENT_BUFFER = 122
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

# Display settings
ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x01
CDS_NORESET = 0x10000000
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000
DISP_CHANGE_SUCCESSFUL = 0

# ---------------------------------------------------------------------------
# Parsec VDD constants (from parsec-vdd.h)
# ---------------------------------------------------------------------------

VDD_IOCTL_ADD = 0x0022E004
VDD_IOCTL_REMOVE = 0x0022A008
VDD_IOCTL_UPDATE = 0x0022A00C
VDD_IOCTL_VERSION = 0x0022E010

VDD_MAX_DISPLAYS = 8
VDD_PING_INTERVAL_S = 0.100  # 100ms

# Parsec display adapter name prefix
VDD_DISPLAY_PREFIX = "PSCCDD"

# ---------------------------------------------------------------------------
# GUID structures
# ---------------------------------------------------------------------------

class GUID(Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", c_byte * 8),
    ]


# Parsec VDD adapter interface GUID: {00b41627-04c4-429e-a26e-0265cf50c8fa}
VDD_ADAPTER_GUID = GUID(
    0x00B41627, 0x04C4, 0x429E,
    (c_byte * 8)(0xA2, 0x6E, 0x02, 0x65, 0xCF, 0x50, 0xC8, 0xFA),
)

# ---------------------------------------------------------------------------
# SetupAPI structures
# ---------------------------------------------------------------------------

class SP_DEVICE_INTERFACE_DATA(Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", POINTER(c_ulong)),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(Structure):
    """Variable-length structure. We allocate a large DevicePath buffer."""
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("DevicePath", c_wchar * 512),
    ]


# ---------------------------------------------------------------------------
# DEVMODE for ChangeDisplaySettingsEx
# ---------------------------------------------------------------------------

class DEVMODEW(Structure):
    # Simplified -- only the fields we need
    _fields_ = [
        ("dmDeviceName", c_wchar * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        # Position
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        # Color
        ("dmColor", wt.SHORT),
        ("dmDuplex", wt.SHORT),
        ("dmYResolution", wt.SHORT),
        ("dmTTOption", wt.SHORT),
        ("dmCollate", wt.SHORT),
        ("dmFormName", c_wchar * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        # Extended fields (ICM, media, etc.) -- padding to full size
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


class DISPLAY_DEVICEW(Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", c_wchar * 32),
        ("DeviceString", c_wchar * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", c_wchar * 128),
        ("DeviceKey", c_wchar * 128),
    ]


# ---------------------------------------------------------------------------
# OVERLAPPED for async DeviceIoControl
# ---------------------------------------------------------------------------

class OVERLAPPED(Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


# ---------------------------------------------------------------------------
# Win32 function bindings
# ---------------------------------------------------------------------------

kernel32 = windll.kernel32
setupapi = windll.setupapi
user32 = windll.user32

# Use c_void_p for all HANDLE returns -- wt.HANDLE is c_int on some
# Python builds and overflows on 64-bit Windows when the kernel returns
# a large handle value.
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
    wt.DWORD, wt.DWORD, ctypes.c_void_p,
]
kernel32.DeviceIoControl.restype = wt.BOOL
kernel32.DeviceIoControl.argtypes = [
    ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD,
    POINTER(wt.DWORD), ctypes.c_void_p,
]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR,
]
kernel32.GetOverlappedResultEx.restype = wt.BOOL
kernel32.GetOverlappedResultEx.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, POINTER(wt.DWORD),
    wt.DWORD, wt.BOOL,
]
kernel32.GetLastError.restype = wt.DWORD

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    wt.DWORD, ctypes.c_void_p,
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    wt.DWORD, POINTER(wt.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wt.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

user32.EnumDisplayDevicesW.restype = wt.BOOL
user32.EnumDisplaySettingsW.restype = wt.BOOL
user32.ChangeDisplaySettingsExW.restype = ctypes.c_long

# ---------------------------------------------------------------------------
# State file for cross-process coordination
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / ".vdd_state"


# ---------------------------------------------------------------------------
# VirtualDisplay class
# ---------------------------------------------------------------------------

class VirtualDisplay:
    """Manages a Parsec virtual display at exact pixel dimensions.

    The driver requires a 100ms keepalive ping or it unplugs the display.
    This class runs the ping loop in a daemon thread and cleans up on stop().

    Args:
        width: Display width in pixels (default 1024).
        height: Display height in pixels (default 768).
        refresh_rate: Refresh rate in Hz (default 60).
    """

    def __init__(self, width: int = 1024, height: int = 768, refresh_rate: int = 60):
        self.width = width
        self.height = height
        self.refresh_rate = refresh_rate

        self._handle: Optional[int] = None
        self._display_index: Optional[int] = None
        self._device_name: Optional[str] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False

    @property
    def device_name(self) -> Optional[str]:
        """Windows display device name (e.g., \\\\.\\DISPLAY5)."""
        return self._device_name

    @property
    def display_index(self) -> Optional[int]:
        """Parsec VDD display index (0-7)."""
        return self._display_index

    @property
    def is_active(self) -> bool:
        return self._started and not self._stop_event.is_set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> "VirtualDisplay":
        """Create the virtual display and set its resolution.

        Returns self for chaining.

        Raises:
            RuntimeError: If the Parsec VDD driver is not installed or
                another instance is already running.
        """
        if self._started:
            raise RuntimeError("Virtual display already started")

        # Register custom resolution in registry before opening
        _ensure_custom_resolution(self.width, self.height, self.refresh_rate)

        log.info("Opening Parsec VDD device handle...")
        self._handle = _open_device_handle()

        # Query driver version for logging
        version = _ioctl(self._handle, VDD_IOCTL_VERSION)
        log.info("Parsec VDD driver version: 0.%d", version)

        # Start keepalive ping thread BEFORE adding display
        self._stop_event.clear()
        self._ping_thread = threading.Thread(
            target=self._ping_loop, daemon=True, name="vdd-ping"
        )
        self._ping_thread.start()
        log.info("Keepalive ping thread started (100ms interval)")

        # Add the virtual display
        self._display_index = _ioctl(self._handle, VDD_IOCTL_ADD)
        log.info("Added virtual display at index %d", self._display_index)

        # Give Windows a moment to enumerate the new display
        time.sleep(1.0)

        # Find the Parsec display device name
        self._device_name = _find_parsec_display()
        if not self._device_name:
            log.warning("Could not find Parsec display device -- "
                        "resolution will use default")
        else:
            log.info("Parsec display device: %s", self._device_name)
            # Set resolution (with fallback to closest supported mode)
            try:
                _set_resolution(self._device_name, self.width, self.height,
                                self.refresh_rate)
                log.info("Resolution set to %dx%d@%dHz",
                         self.width, self.height, self.refresh_rate)
            except RuntimeError:
                # Requested mode not available -- find closest match
                actual = _set_closest_resolution(
                    self._device_name, self.width, self.height,
                )
                if actual:
                    self.width, self.height = actual[0], actual[1]
                    log.info("Fell back to closest mode: %dx%d@%dHz",
                             actual[0], actual[1], actual[2])
                else:
                    log.warning("Could not set any resolution -- "
                                "display will use driver default")

        self._started = True
        _write_state(self._display_index, self.width, self.height,
                     self._device_name)
        log.info("[OK] Virtual display active: %dx%d on %s",
                 self.width, self.height, self._device_name or "unknown")
        return self

    def stop(self):
        """Remove the virtual display and clean up."""
        if not self._started:
            return

        log.info("Stopping virtual display...")

        # Stop ping thread first
        self._stop_event.set()
        if self._ping_thread and self._ping_thread.is_alive():
            self._ping_thread.join(timeout=2.0)

        # Remove the display
        if self._handle and self._display_index is not None:
            try:
                _ioctl_remove(self._handle, self._display_index)
                log.info("Removed virtual display at index %d",
                         self._display_index)
            except OSError as e:
                log.warning("Failed to remove display: %s", e)

        # Close device handle
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None

        self._display_index = None
        self._device_name = None
        self._started = False
        _clear_state()
        log.info("[OK] Virtual display stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ping_loop(self):
        """Send keepalive pings every 100ms until stopped."""
        while not self._stop_event.is_set():
            try:
                if self._handle:
                    _ioctl(self._handle, VDD_IOCTL_UPDATE)
            except OSError:
                # Handle may have been closed during shutdown
                if not self._stop_event.is_set():
                    log.warning("Ping failed -- handle may be closed")
                break
            self._stop_event.wait(VDD_PING_INTERVAL_S)


# ---------------------------------------------------------------------------
# Low-level functions
# ---------------------------------------------------------------------------

def _open_device_handle() -> int:
    """Open a handle to the Parsec VDD adapter via SetupAPI.

    Returns:
        Device handle (int).

    Raises:
        RuntimeError: If driver not found or handle cannot be opened.
    """
    hdev = setupapi.SetupDiGetClassDevsW(
        byref(VDD_ADAPTER_GUID),
        None,
        None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if hdev == INVALID_HANDLE_VALUE:
        raise RuntimeError(
            "Parsec VDD driver not found. Is it installed? "
            "Check: C:\\Program Files\\Parsec Virtual Display Driver"
        )

    try:
        iface_data = SP_DEVICE_INTERFACE_DATA()
        iface_data.cbSize = sizeof(SP_DEVICE_INTERFACE_DATA)

        if not setupapi.SetupDiEnumDeviceInterfaces(
            hdev, None, byref(VDD_ADAPTER_GUID), 0, byref(iface_data)
        ):
            raise RuntimeError(
                "Parsec VDD adapter interface not found. "
                "Driver may need reinstalling."
            )

        # Get required buffer size
        required_size = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, byref(iface_data), None, 0, byref(required_size), None
        )

        # Get device path
        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
        # cbSize must be 8 on 64-bit Windows (size of fixed part)
        detail.cbSize = 8

        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, byref(iface_data), byref(detail),
            required_size, None, None
        ):
            err = kernel32.GetLastError()
            raise RuntimeError(
                f"Failed to get device interface detail (error {err})"
            )

        device_path = detail.DevicePath
        log.debug("Device path: %s", device_path)

    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)

    # Open the device
    handle = kernel32.CreateFileW(
        device_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        err = kernel32.GetLastError()
        raise RuntimeError(
            f"Failed to open Parsec VDD device (error {err}). "
            "Try running as administrator."
        )

    return handle


def _ioctl(handle: int, code: int, in_data: bytes = b"") -> int:
    """Send a DeviceIoControl to the Parsec VDD with overlapped I/O.

    Args:
        handle: Device handle from _open_device_handle().
        code: IOCTL code (VDD_IOCTL_*).
        in_data: Optional input bytes (padded to 32 bytes).

    Returns:
        DWORD output value.
    """
    in_buf = (c_byte * 32)()
    if in_data:
        for i, b in enumerate(in_data[:32]):
            in_buf[i] = b

    out_buf = wt.DWORD(0)
    bytes_returned = wt.DWORD(0)

    # Create overlapped event
    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise OSError("Failed to create event for overlapped I/O")

    try:
        ov = OVERLAPPED()
        ctypes.memset(byref(ov), 0, sizeof(ov))
        ov.hEvent = event

        result = kernel32.DeviceIoControl(
            handle, code,
            byref(in_buf), sizeof(in_buf),
            byref(out_buf), sizeof(out_buf),
            byref(bytes_returned),
            byref(ov),
        )

        if not result:
            err = kernel32.GetLastError()
            # ERROR_IO_PENDING = 997
            if err != 997:
                raise OSError(f"DeviceIoControl failed (error {err})")

        # Wait for completion (5 second timeout)
        if not kernel32.GetOverlappedResultEx(
            handle, byref(ov), byref(bytes_returned), 5000, False
        ):
            err = kernel32.GetLastError()
            raise OSError(f"Overlapped I/O failed (error {err})")

    finally:
        kernel32.CloseHandle(event)

    return out_buf.value


def _ioctl_remove(handle: int, index: int):
    """Remove a virtual display by index.

    The index is encoded as 16-bit big-endian swap per parsec-vdd.h:
        UINT16 indexData = ((index & 0xFF) << 8) | ((index >> 8) & 0xFF)
    """
    # For indices 0-7, this puts the index byte at offset 1
    swapped = ((index & 0xFF) << 8) | ((index >> 8) & 0xFF)
    in_data = struct.pack("<H", swapped)  # Little-endian UINT16
    _ioctl(handle, VDD_IOCTL_REMOVE, in_data)
    # Send an update ping to confirm removal
    _ioctl(handle, VDD_IOCTL_UPDATE)


def _find_parsec_display() -> Optional[str]:
    """Find the Windows device name for the Parsec virtual display.

    Enumerates display adapters looking for one with a Parsec monitor
    (DeviceID containing 'PSCCDD').

    Returns:
        Device name string (e.g., '\\\\.\\DISPLAY5') or None.
    """
    DISPLAY_DEVICE_ATTACHED = 0x01

    adapter = DISPLAY_DEVICEW()
    adapter.cb = sizeof(DISPLAY_DEVICEW)

    monitor = DISPLAY_DEVICEW()
    monitor.cb = sizeof(DISPLAY_DEVICEW)

    idx = 0
    while user32.EnumDisplayDevicesW(None, idx, byref(adapter), 0):
        # Check if this adapter has an attached Parsec monitor
        if adapter.StateFlags & DISPLAY_DEVICE_ATTACHED:
            mon_idx = 0
            while user32.EnumDisplayDevicesW(
                adapter.DeviceName, mon_idx, byref(monitor), 0
            ):
                if VDD_DISPLAY_PREFIX in monitor.DeviceID:
                    return adapter.DeviceName
                mon_idx += 1

        # Also check the adapter's DeviceString
        if "Parsec" in adapter.DeviceString:
            if adapter.StateFlags & DISPLAY_DEVICE_ATTACHED:
                return adapter.DeviceName

        idx += 1

    return None


def _ensure_custom_resolution(width: int, height: int, refresh_rate: int = 60):
    """Register a custom resolution in the Parsec VDD registry.

    The driver reads custom modes from HKLM\\SOFTWARE\\Parsec\\vdd\\{0-4}.
    Each subkey has width, height, hz DWORDs. Up to 5 custom resolutions.

    This writes to slot 0. Requires the driver to be reloaded (display
    add/remove cycle) to pick up new modes.
    """
    import winreg

    key_path = r"SOFTWARE\Parsec\vdd"

    try:
        # Create parent key if needed
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, key_path,
            access=winreg.KEY_WRITE | winreg.KEY_READ,
        )
        winreg.CloseKey(key)

        # Write to slot 0
        slot_path = f"{key_path}\\0"
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, slot_path,
            access=winreg.KEY_WRITE,
        )
        winreg.SetValueEx(key, "width", 0, winreg.REG_DWORD, width)
        winreg.SetValueEx(key, "height", 0, winreg.REG_DWORD, height)
        winreg.SetValueEx(key, "hz", 0, winreg.REG_DWORD, refresh_rate)
        winreg.CloseKey(key)
        log.info("Registered custom resolution %dx%d@%dHz in registry slot 0",
                 width, height, refresh_rate)
        return True
    except PermissionError:
        log.warning("Cannot write to HKLM registry -- run as administrator "
                    "to register custom resolutions")
        return False
    except Exception as e:
        log.warning("Failed to register custom resolution: %s", e)
        return False


def _list_supported_modes(device_name: str) -> list:
    """Enumerate all display modes supported by a device.

    Returns list of (width, height, hz) tuples.
    """
    modes = []
    devmode = DEVMODEW()
    devmode.dmSize = sizeof(DEVMODEW)

    idx = 0
    while user32.EnumDisplaySettingsW(device_name, idx, byref(devmode)):
        modes.append((devmode.dmPelsWidth, devmode.dmPelsHeight,
                      devmode.dmDisplayFrequency))
        idx += 1

    return sorted(set(modes))


def _commit_display_changes():
    """Commit all pending CDS_NORESET display changes atomically.

    This avoids the all-monitor flash that CDS_GLOBAL causes.
    """
    user32.ChangeDisplaySettingsExW(None, None, None, 0, None)


def _set_resolution(device_name: str, width: int, height: int,
                    refresh_rate: int = 60):
    """Set the resolution on a display device.

    If the requested mode is not supported, tries to register it via
    registry and falls back to the closest available mode.

    Args:
        device_name: Windows display device name.
        width: Width in pixels.
        height: Height in pixels.
        refresh_rate: Hz (default 60).

    Raises:
        RuntimeError: If no acceptable mode can be set.
    """
    devmode = DEVMODEW()
    devmode.dmSize = sizeof(DEVMODEW)

    # Read current mode as base
    user32.EnumDisplaySettingsW(
        device_name, ENUM_CURRENT_SETTINGS, byref(devmode)
    )

    # Set desired resolution
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmDisplayFrequency = refresh_rate
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY

    result = user32.ChangeDisplaySettingsExW(
        device_name, byref(devmode), None,
        CDS_UPDATEREGISTRY | CDS_NORESET, None,
    )

    if result == DISP_CHANGE_SUCCESSFUL:
        _commit_display_changes()
        return

    # BADMODE -- mode not in driver's supported list
    log.warning("Mode %dx%d@%d not directly supported, "
                "checking available modes...", width, height, refresh_rate)

    modes = _list_supported_modes(device_name)
    if modes:
        log.info("Available modes: %s",
                 ", ".join(f"{w}x{h}@{hz}" for w, h, hz in modes[:20]))

    # Check if exact resolution exists at any refresh rate
    for w, h, hz in modes:
        if w == width and h == height:
            log.info("Found matching resolution at %dHz", hz)
            devmode.dmDisplayFrequency = hz
            result = user32.ChangeDisplaySettingsExW(
                device_name, byref(devmode), None,
                CDS_UPDATEREGISTRY | CDS_NORESET, None,
            )
            if result == DISP_CHANGE_SUCCESSFUL:
                _commit_display_changes()
                return

    # Try without specifying refresh rate
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    result = user32.ChangeDisplaySettingsExW(
        device_name, byref(devmode), None,
        CDS_UPDATEREGISTRY | CDS_NORESET, None,
    )
    if result == DISP_CHANGE_SUCCESSFUL:
        _commit_display_changes()
        log.info("Set resolution without explicit refresh rate")
        return

    # Last resort: log available modes and raise
    error_map = {
        -1: "DISP_CHANGE_FAILED",
        -2: "DISP_CHANGE_BADMODE",
        -3: "DISP_CHANGE_NOTUPDATED",
        -4: "DISP_CHANGE_BADFLAGS",
        -5: "DISP_CHANGE_BADPARAM",
        1: "DISP_CHANGE_RESTART (reboot required)",
    }
    desc = error_map.get(result, f"unknown ({result})")
    raise RuntimeError(
        f"ChangeDisplaySettingsEx failed: {desc}. "
        f"Requested {width}x{height}@{refresh_rate}Hz on {device_name}. "
        f"Available modes: {modes[:10]}"
    )


def _set_closest_resolution(device_name: str, target_w: int,
                            target_h: int) -> Optional[tuple]:
    """Find and set the closest available resolution to the target.

    Prefers modes that are >= target size (so nothing gets clipped),
    scored by total pixel difference. Only considers 60Hz modes.

    Returns:
        (width, height, hz) tuple of the mode that was set, or None.
    """
    modes = _list_supported_modes(device_name)
    if not modes:
        return None

    # Prefer 60Hz modes for stability
    candidates = [(w, h, hz) for w, h, hz in modes if hz == 60]
    if not candidates:
        candidates = modes

    # Remove duplicates and sort by distance to target
    candidates = sorted(set(candidates),
                        key=lambda m: abs(m[0] - target_w) + abs(m[1] - target_h))

    devmode = DEVMODEW()
    devmode.dmSize = sizeof(DEVMODEW)

    for w, h, hz in candidates:
        user32.EnumDisplaySettingsW(
            device_name, ENUM_CURRENT_SETTINGS, byref(devmode)
        )
        devmode.dmPelsWidth = w
        devmode.dmPelsHeight = h
        devmode.dmDisplayFrequency = hz
        devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY

        result = user32.ChangeDisplaySettingsExW(
            device_name, byref(devmode), None,
            CDS_UPDATEREGISTRY | CDS_NORESET, None,
        )
        if result == DISP_CHANGE_SUCCESSFUL:
            _commit_display_changes()
            return (w, h, hz)

    return None


# ---------------------------------------------------------------------------
# State file helpers (for cross-process stop command)
# ---------------------------------------------------------------------------

def _write_state(index: int, width: int, height: int,
                 device_name: Optional[str]):
    """Write current VDD state to a file so the CLI stop command works."""
    STATE_FILE.write_text(
        f"{index}\n{width}\n{height}\n{device_name or 'unknown'}\n"
        f"{int(time.time())}\n"
    )


def _clear_state():
    """Remove state file."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def read_state() -> Optional[dict]:
    """Read current VDD state (for status/stop commands).

    Returns:
        Dict with index, width, height, device_name, started_at or None.
    """
    if not STATE_FILE.exists():
        return None
    try:
        lines = STATE_FILE.read_text().strip().split("\n")
        return {
            "index": int(lines[0]),
            "width": int(lines[1]),
            "height": int(lines[2]),
            "device_name": lines[3],
            "started_at": int(lines[4]),
        }
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Convenience: check driver status without starting
# ---------------------------------------------------------------------------

def check_driver() -> dict:
    """Check if the Parsec VDD driver is installed and accessible.

    Returns:
        Dict with 'installed', 'version', 'error' keys.
    """
    result = {"installed": False, "version": None, "error": None}

    try:
        handle = _open_device_handle()
    except RuntimeError as e:
        result["error"] = str(e)
        return result

    try:
        version = _ioctl(handle, VDD_IOCTL_VERSION)
        result["installed"] = True
        result["version"] = f"0.{version}"
    except OSError as e:
        result["error"] = str(e)
    finally:
        kernel32.CloseHandle(handle)

    return result
