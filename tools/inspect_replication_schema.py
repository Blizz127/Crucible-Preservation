#!/usr/bin/env python3
"""Read retail component replication counts from a user-launched Linux/Wine client.

No debugger attachment, process writes, game launch, or process termination.
Only component names and four record-size counters are emitted. Offsets are
specific to the inspected retail build; instruction and vtable guards reject
unknown layouts. Requires pefile, as do the existing executable inspection tools.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct

import pefile


def component_names(pe):
    # ComponentType-to-name dispatcher at RVA 4ba1d00, 180-entry jump table.
    if pe.get_data(0xac2e40, 3) != bytes.fromhex("b0a9c3"):
        raise ValueError("unsupported PlayerComponent type getter")
    names = []
    for index in range(180):
        target, = struct.unpack("<I", pe.get_data(0x4ba22cc + index * 4, 4))
        code = pe.get_data(target, 8)
        if code[:3] != bytes.fromhex("488d05") or code[7:] != b"\xc3":
            raise ValueError("unsupported component-name dispatcher")
        relative, = struct.unpack_from("<i", code, 3)
        name = pe.get_data(target + 7 + relative, 128).split(b"\0", 1)[0]
        names.append(name.decode("ascii"))
    if names[58] != "GameModeComponent" or names[169] != "PlayerComponent":
        raise ValueError("unsupported component table")
    return names


def find_client(executable):
    canonical = executable.resolve()
    matches = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            rows = (process / "maps").read_text().splitlines()
        except (OSError, UnicodeError):
            continue
        for row in rows:
            fields = row.split(maxsplit=5)
            if (len(fields) != 6 or not fields[5].endswith("/Crucible.exe")
                    or Path(fields[5]).resolve() != canonical):
                continue
            # Wine also maps executable data as a normal file. Only an image
            # header mapping at file offset zero can identify the image base.
            if int(fields[2], 16) == 0:
                matches.append((int(process.name), int(fields[0].split("-")[0], 16)))
    if len(matches) != 1:
        raise ValueError(f"expected one client image; found {len(matches)}")
    return matches[0]


def inspect(executable):
    with pefile.PE(str(executable), fast_load=True) as pe:
        names = component_names(pe)
    pid, base = find_client(executable)
    fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    try:
        def read(address, size):
            data = os.pread(fd, size, address)
            if len(data) != size:
                raise ValueError("client exited or returned a short read")
            return data

        def pointer(address):
            return struct.unpack("<Q", read(address, 8))[0]

        if read(base, 2) != b"MZ":
            raise ValueError("client image header not ready")
        game = pointer(base + 0x760bb18)
        if not game:
            raise ValueError("client game instance not initialized")
        manager = pointer(game + 0x60)
        if not manager or pointer(manager) - base != 0x67a1370:
            raise ValueError("EntityManager not initialized or unsupported layout")
        # PlayerComponent::NetworkAttach, ac3f88..ac3ffc: manager+6a0
        # contains EntityComponentInfoManager; entries begin at +2760,
        # stride 1d8. Descriptor+150 holds four u16 direction counts.
        components = []
        for index, name in enumerate(names):
            entry = manager + 0x6a0 + 0x2760 + index * 0x1d8
            counts = struct.unpack("<4H", read(entry + 0x150, 8))
            if any(count > 1024 for count in counts):
                raise ValueError(f"invalid replication size for {name}")
            components.append({"type_id": index, "name": name,
                               "expanded_record_bits": list(counts)})
        if components[58]["expanded_record_bits"][0] != 14:
            raise ValueError("GameMode schema not initialized or unexpected layout")
        return {"observed_at": datetime.now(timezone.utc).isoformat(),
                "pid": pid, "directions": ["server_to_clients", "server_to_servers",
                                            "server_to_autonomous", "client_to_servers"],
                "components": components}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, help="absolute installed Crucible.exe path")
    args = parser.parse_args()
    try:
        result = inspect(args.executable.absolute())
    except (OSError, ValueError, struct.error, pefile.PEFormatError) as error:
        parser.exit(1, f"Schema not available: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
