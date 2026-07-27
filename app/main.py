"""Desktop entry point for QSign."""

from collections.abc import Callable
from pathlib import Path
import ctypes
import os
import re
import shutil
import struct
import sys
import threading

APP_TITLE = "qSign by Queen Srl - queensrl.net"
APP_PRODUCT_NAME = "QSign"
APP_PUBLISHER = "Queen Srl"


def run() -> None:
    """Start the Flet desktop application."""
    project_root = Path(__file__).resolve().parent.parent
    _prepare_flet_runtime_metadata(project_root)

    import flet as ft

    from app.qsign_application import QSignApplication

    target: Callable[[ft.Page], None] = QSignApplication().main
    ft.run(
        main=target,
        before_main=_prepare_flet_window,
        assets_dir=str(project_root / "resources"),
    )


def _prepare_flet_window(page: object) -> None:
    setattr(page, "title", APP_TITLE)
    if sys.platform != "win32":
        return
    icon_path = (
        Path(__file__).resolve().parent.parent
        / "resources"
        / "icons"
        / "favicon.ico"
    )
    if not icon_path.is_file():
        return
    from ui.main_view import MainView

    threading.Thread(
        target=MainView._apply_windows_window_icon,
        args=(str(icon_path), APP_TITLE),
        daemon=True,
    ).start()


def _prepare_flet_runtime_metadata(project_root: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        import flet_desktop

        cache_dir = Path(flet_desktop.ensure_client_cached())
        flet_dir = _prepare_qsign_flet_runtime(cache_dir, _read_app_version(project_root))
        flet_exe = flet_dir / "flet.exe"
        if not flet_exe.is_file():
            return
        _set_windows_executable_metadata(
            flet_exe,
            version=_read_app_version(project_root),
            icon_path=project_root / "resources" / "icons" / "favicon.ico",
        )
        os.environ["FLET_VIEW_PATH"] = str(flet_dir)
    except Exception:
        return


def _prepare_qsign_flet_runtime(cache_dir: Path, version: str) -> Path:
    source_flet_dir = cache_dir / "flet"
    source_flet_exe = source_flet_dir / "flet.exe"
    if not source_flet_exe.is_file():
        return source_flet_dir

    runtime_root = _qsign_flet_runtime_root()
    runtime_dir = runtime_root / _safe_runtime_directory_name(cache_dir.name, version)
    runtime_flet_dir = runtime_dir / "flet"
    runtime_flet_exe = runtime_flet_dir / "flet.exe"
    stamp_path = runtime_dir / ".source"
    source_stamp = f"{cache_dir.resolve()}\n{version}\n"
    try:
        if (
            runtime_flet_exe.is_file()
            and stamp_path.is_file()
            and stamp_path.read_text(encoding="utf-8") == source_stamp
        ):
            return runtime_flet_dir
    except OSError:
        pass

    staging_dir = runtime_root / f"{runtime_dir.name}.staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_flet_dir = staging_dir / "flet"
    shutil.copytree(source_flet_dir, staging_flet_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / ".source").write_text(source_stamp, encoding="utf-8")
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    shutil.move(str(staging_dir), str(runtime_dir))
    return runtime_flet_dir


def _qsign_flet_runtime_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_PRODUCT_NAME / "flet-runtime"
    return Path.home() / "AppData" / "Local" / APP_PRODUCT_NAME / "flet-runtime"


def _safe_runtime_directory_name(cache_name: str, version: str) -> str:
    raw = f"{cache_name}-{version}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-") or "runtime"


def _read_app_version(project_root: Path) -> str:
    config_path = project_root / "config" / "app.yaml"
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            match = re.match(r'\s*version\s*:\s*["\']?([^"\']+)["\']?\s*$', line)
            if match:
                return match.group(1).strip()
    except OSError:
        pass
    return "0.0.0"


def _set_windows_executable_metadata(
    path: Path, *, version: str, icon_path: Path | None = None
) -> None:
    resource = _windows_version_resource(
        version=version,
        strings={
            "CompanyName": APP_PUBLISHER,
            "FileDescription": APP_PRODUCT_NAME,
            "FileVersion": version,
            "InternalName": APP_PRODUCT_NAME,
            "OriginalFilename": "flet.exe",
            "ProductName": APP_PRODUCT_NAME,
            "ProductVersion": version,
        },
    )
    icon_resources = _windows_icon_resources(icon_path) if icon_path else None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = (ctypes.c_wchar_p, ctypes.c_bool)
    begin.restype = ctypes.c_void_p
    update = kernel32.UpdateResourceW
    update.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_ushort,
        ctypes.c_void_p,
        ctypes.c_uint,
    )
    update.restype = ctypes.c_bool
    end = kernel32.EndUpdateResourceW
    end.argtypes = (ctypes.c_void_p, ctypes.c_bool)
    end.restype = ctypes.c_bool

    handle = begin(str(path), False)
    if not handle:
        raise OSError(ctypes.get_last_error(), "BeginUpdateResourceW failed")
    buffer = ctypes.create_string_buffer(resource)
    discard = True
    try:
        if not update(
            handle,
            ctypes.c_wchar_p(16),
            ctypes.c_wchar_p(1),
            0x0409,
            buffer,
            len(resource),
        ):
            raise OSError(ctypes.get_last_error(), "UpdateResourceW failed")
        if icon_resources is not None:
            for icon_id, icon_data in icon_resources.icons:
                icon_buffer = ctypes.create_string_buffer(icon_data)
                if not update(
                    handle,
                    ctypes.c_wchar_p(3),
                    ctypes.c_wchar_p(icon_id),
                    0x0409,
                    icon_buffer,
                    len(icon_data),
                ):
                    raise OSError(
                        ctypes.get_last_error(), "UpdateResourceW icon failed"
                    )
            group_buffer = ctypes.create_string_buffer(icon_resources.group)
            if not update(
                handle,
                ctypes.c_wchar_p(14),
                ctypes.c_wchar_p(1),
                0x0409,
                group_buffer,
                len(icon_resources.group),
            ):
                raise OSError(
                    ctypes.get_last_error(), "UpdateResourceW group icon failed"
                )
        discard = False
    finally:
        if not end(handle, discard):
            raise OSError(ctypes.get_last_error(), "EndUpdateResourceW failed")


def _windows_version_resource(*, version: str, strings: dict[str, str]) -> bytes:
    version_tuple = _version_tuple(version)
    fixed_file_info = struct.pack(
        "<13I",
        0xFEEF04BD,
        0x00010000,
        (version_tuple[0] << 16) | version_tuple[1],
        (version_tuple[2] << 16) | version_tuple[3],
        (version_tuple[0] << 16) | version_tuple[1],
        (version_tuple[2] << 16) | version_tuple[3],
        0x0000003F,
        0x00000000,
        0x00040004,
        0x00000001,
        0x00000000,
        0x00000000,
        0x00000000,
    )
    string_table = _version_node(
        "040904B0",
        b"".join(_version_string(key, value) for key, value in strings.items()),
        value=b"",
        value_length=0,
        value_type=1,
    )
    string_file_info = _version_node(
        "StringFileInfo",
        string_table,
        value=b"",
        value_length=0,
        value_type=1,
    )
    translation = struct.pack("<HH", 0x0409, 0x04B0)
    var_file_info = _version_node(
        "VarFileInfo",
        _version_node(
            "Translation",
            b"",
            value=translation,
            value_length=len(translation),
            value_type=0,
        ),
        value=b"",
        value_length=0,
        value_type=1,
    )
    return _version_node(
        "VS_VERSION_INFO",
        string_file_info + var_file_info,
        value=fixed_file_info,
        value_length=len(fixed_file_info),
        value_type=0,
    )


class _WindowsIconResources:
    def __init__(self, *, icons: list[tuple[int, bytes]], group: bytes) -> None:
        self.icons = icons
        self.group = group


def _windows_icon_resources(icon_path: Path) -> _WindowsIconResources | None:
    try:
        data = icon_path.read_bytes()
    except OSError:
        return None
    if len(data) < 6:
        return None
    reserved, image_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or image_type != 1 or count == 0:
        return None

    entries: list[tuple[bytes, bytes]] = []
    for index in range(count):
        entry_offset = 6 + index * 16
        if entry_offset + 16 > len(data):
            return None
        icon_header = data[entry_offset : entry_offset + 12]
        image_size, image_offset = struct.unpack_from("<II", data, entry_offset + 8)
        if image_offset + image_size > len(data):
            return None
        entries.append((icon_header, data[image_offset : image_offset + image_size]))

    icons = [(index + 1, image) for index, (_, image) in enumerate(entries)]
    group_entries = [
        header + struct.pack("<H", icon_id)
        for icon_id, (header, _) in zip(range(1, len(entries) + 1), entries)
    ]
    group = struct.pack("<HHH", 0, 1, len(entries)) + b"".join(group_entries)
    return _WindowsIconResources(icons=icons, group=group)


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", version)[:4]]
    return tuple((parts + [0, 0, 0, 0])[:4])


def _version_string(key: str, value: str) -> bytes:
    encoded = _utf16z(value)
    return _version_node(
        key,
        b"",
        value=encoded,
        value_length=len(encoded) // 2,
        value_type=1,
    )


def _version_node(
    key: str,
    children: bytes,
    *,
    value: bytes,
    value_length: int,
    value_type: int,
) -> bytes:
    header = struct.pack("<HHH", 0, value_length, value_type) + _utf16z(key)
    payload = _pad4(header) + value
    payload = _pad4(payload) + children
    return struct.pack("<H", len(payload)) + payload[2:]


def _utf16z(value: str) -> bytes:
    return (value + "\0").encode("utf-16le")


def _pad4(data: bytes) -> bytes:
    return data + (b"\0" * ((4 - len(data) % 4) % 4))


if __name__ == "__main__":
    run()
