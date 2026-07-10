"""List exported function names from a PE DLL."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _cstr(data: bytes, offset: int) -> str:
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("ascii", errors="replace")


def _rva_to_offset(sections: list[tuple[int, int, int, int]], rva: int) -> int:
    for virtual_address, virtual_size, raw_pointer, raw_size in sections:
        size = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + size:
            return raw_pointer + (rva - virtual_address)
    raise ValueError(f"RVA not found: 0x{rva:x}")


def list_exports(path: Path) -> list[str]:
    data = path.read_bytes()
    pe_offset = _u32(data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        raise ValueError("Not a PE file")

    file_header = pe_offset + 4
    section_count = _u16(data, file_header + 2)
    optional_header_size = _u16(data, file_header + 16)
    optional_header = file_header + 20
    magic = _u16(data, optional_header)
    data_directory = optional_header + (112 if magic == 0x20B else 96)
    export_rva = _u32(data, data_directory)
    if export_rva == 0:
        return []

    sections_offset = optional_header + optional_header_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        section = sections_offset + index * 40
        virtual_size = _u32(data, section + 8)
        virtual_address = _u32(data, section + 12)
        raw_size = _u32(data, section + 16)
        raw_pointer = _u32(data, section + 20)
        sections.append((virtual_address, virtual_size, raw_pointer, raw_size))

    export_offset = _rva_to_offset(sections, export_rva)
    name_count = _u32(data, export_offset + 24)
    names_rva = _u32(data, export_offset + 32)
    names_offset = _rva_to_offset(sections, names_rva)
    names = []
    for index in range(name_count):
        name_rva = _u32(data, names_offset + index * 4)
        names.append(_cstr(data, _rva_to_offset(sections, name_rva)))
    return names


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: list_dll_exports.py <dll>")
        return 2
    for name in list_exports(Path(sys.argv[1])):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
