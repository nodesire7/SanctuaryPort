"""List the export names of a PE library, so the SDL3 proxy can forward all of them.

usage: python exports.py SDL3.dll > exports.json
"""
import json
import struct
import sys


def exports(path: str) -> list[str]:
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit(f"{path}: not a PE library")
    sections = struct.unpack_from("<H", data, pe + 6)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    plus = magic == 0x20B
    directory = optional + (112 if plus else 96)
    export_rva = struct.unpack_from("<I", data, directory)[0]
    table_off = optional + (240 if plus else 224)
    mapped = []
    for index in range(sections):
        _name, vsize, vaddr, rsize, raddr = struct.unpack_from("<8sIIII", data,
                                                              table_off + index * 40)
        mapped.append((vaddr, max(vsize, rsize), raddr))

    def offset(rva: int) -> int:
        for vaddr, size, raddr in mapped:
            if vaddr <= rva < vaddr + size:
                return raddr + (rva - vaddr)
        raise SystemExit(f"{path}: rva {rva:#x} is not mapped")

    at = offset(export_rva)
    count = struct.unpack_from("<I", data, at + 24)[0]
    names_rva = struct.unpack_from("<I", data, at + 32)[0]
    names = []
    for index in range(count):
        name_rva = struct.unpack_from("<I", data, offset(names_rva) + index * 4)[0]
        start = offset(name_rva)
        end = data.index(b"\0", start)
        names.append(data[start:end].decode("latin-1"))
    return sorted(names)


if __name__ == "__main__":
    json.dump(exports(sys.argv[1]), sys.stdout)
