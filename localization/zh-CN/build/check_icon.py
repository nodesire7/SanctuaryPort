"""Check that Windows can resolve each upstream icon image from the launcher's icon group."""
import struct
import sys
from pathlib import Path

import pefile


def verify(exe, icon):
    ico = Path(icon).read_bytes()
    with pefile.PE(str(exe), fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resources = {entry.id: entry.directory.entries for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}

        def resource(entry):
            data = entry.directory.entries[0].data.struct
            return pe.get_data(data.OffsetToData, data.Size)

        group = resource(resources[14][0])
        images = {entry.id: resource(entry) for entry in resources[3]}
        assert group[:6] == ico[:6], "icon group does not match upstream"
        count = struct.unpack_from("<H", ico, 4)[0]
        for i in range(count):
            src, dest = 6 + i * 16, 6 + i * 14
            # Resource compilers may fill the ICO's unspecified plane count with 1.
            assert ico[src:src+4] == group[dest:dest+4], "icon dimensions differ"
            size, offset = struct.unpack_from("<II", ico, src + 8)
            icon_id = struct.unpack_from("<H", group, dest + 12)[0]
            assert ico[offset:offset+size] == images[icon_id], "embedded icon image differs"
    print(f"Verified {count} upstream icon images in {exe}")


if __name__ == "__main__":
    verify(*sys.argv[1:])
