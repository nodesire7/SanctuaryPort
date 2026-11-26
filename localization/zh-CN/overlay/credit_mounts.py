# Appended to the fork's existing d3_credit archive reader when compiling the runtime module.
def has_expanded_chinese_fonts(*layers):
    """Recognize the installed translation by its font entries without reading the font data."""
    fonts = (r"Font\ExocetLight.fnt", r"Font\SansSerif.fnt")
    seen = set()
    for layer in filter(None, layers):
        for path in archive_paths(layer):
            relative = path.relative_to(layer).as_posix().casefold()
            if relative in seen:
                continue
            seen.add(relative)
            try:
                with d3_cpk.Archive(path) as archive:
                    sizes = tuple(archive.entries[archive.names[name]][1] for name in fonts)
                    if sizes == (486482552, 191604048):
                        return True
            except (OSError, KeyError, ValueError, IndexError, struct.error):
                continue
    return False


def mount_args(layers, cache):
    """Mount a translated copy over the effective asset using the port's file-mount API."""
    import hashlib
    import os
    import shutil
    import tempfile

    if os.environ.get("SANCTUARY_CREDIT_ALIGN") == "0":
        return []
    args = []
    seen = set()
    for layer in layers:
        for source in archive_paths(layer):
            relative = source.relative_to(layer).as_posix()
            if relative.casefold() in seen:
                continue
            seen.add(relative.casefold())
            try:
                stat = source.stat()
                identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{NEW}"
                target = cache / (hashlib.sha256(identity.encode()).hexdigest() + ".cpk")
                if not target.is_file() or target.stat().st_size != stat.st_size:
                    with d3_cpk.Archive(source) as archive:
                        if ENTRY not in archive.names or OLD.encode() not in archive.read(ENTRY):
                            continue
                    cache.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=cache) as scratch:
                        copy = Path(scratch) / "credit.cpk"
                        shutil.copyfile(source, copy)
                        if patch_archive(copy, ENTRY, OLD, NEW) != "patched":
                            continue
                        with d3_cpk.Archive(copy) as archive:
                            if NEW.encode() not in archive.read(ENTRY):
                                continue
                        os.replace(copy, target)
                args.extend(["--mount", f"/app0/{relative}={target.resolve()}"])
            except (OSError, ValueError, IndexError, KeyError, struct.error, zlib.error) as error:
                print(f"Chinese credit: {source.name}: {error}", flush=True)
    return args
