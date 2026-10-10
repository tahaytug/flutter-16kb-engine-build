"""Strict ELF64 LOAD/RELRO check; requires existing read-only protection."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def inspect(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected a little-endian ELF64 library")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[1] != 3:
        raise ValueError("Expected ET_DYN")
    machine, table, entry_size, count = header[2], header[5], header[9], header[10]
    if entry_size < 56 or count == 0 or table + entry_size * count > len(data):
        raise ValueError("Invalid program header table")
    loads, relro = [], []
    for index in range(count):
        kind, flags, offset, address, _, file_size, memory_size, alignment = struct.unpack_from(
            "<IIQQQQQQ", data, table + index * entry_size
        )
        if (kind == 1 and file_size > memory_size) or offset + file_size > len(data):
            raise ValueError("Invalid segment range")
        if kind == 1:
            loads.append({"alignment": alignment, "offset": hex(offset),
                          "address": hex(address),
                          "aligned16KB": alignment >= 16384 and
                          alignment & (alignment - 1) == 0 and
                          offset % 16384 == address % 16384})
        if kind == 0x6474E552:
            relro.append({"endAddress": hex(address + memory_size),
                          "aligned16KB": (address + memory_size) % 16384 == 0})
    return {"library": Path(path).name, "sha256": hashlib.sha256(data).hexdigest(),
            "machine": machine, "loads": loads, "relro": relro,
            "hasRelro": bool(relro),
            "staticChecksPassed": bool(loads) and bool(relro) and
            all(item["aligned16KB"] for item in loads + relro),
            "scope": "ELF LOAD/RELRO only; not APK/AAB or 16 KB runtime acceptance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("library")
    parser.add_argument("--arm64", action="store_true")
    args = parser.parse_args()
    result = inspect(args.library)
    if args.arm64 and result["machine"] != 183:
        raise SystemExit("Expected AArch64 engine")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["staticChecksPassed"] else 1)
