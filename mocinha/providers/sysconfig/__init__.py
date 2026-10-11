"""System settings providers (hostname, locale, keymap, timezone).

Each provider's setting_choices() lists the values a frontend can offer for the
settings it supports, read from the live system (the source of the target's
data); a setting it does not support is absent, so a frontend does not ask for it.
"""

from pathlib import Path
from typing import List

ZONEINFO = "usr/share/zoneinfo"


def zone_names(root: Path) -> List[str]:
    """Zone names under <root>/usr/share/zoneinfo (TZif files; no posix/ or right/ copies)."""
    base = root / ZONEINFO
    zones = []
    for f in base.rglob("*") if base.is_dir() else []:
        rel = f.relative_to(base).as_posix()
        if f.is_file() and rel[0].isupper() and not rel.startswith(("posix/", "right/")):
            try:
                with open(f, "rb") as fh:
                    if fh.read(4) == b"TZif":
                        zones.append(rel)
            except OSError:
                pass
    return sorted(zones)


def keymap_names(root: Path, keymap_dir: str) -> List[str]:
    """Console keymaps (<name>.map or <name>.map.gz) anywhere under <root>/<keymap_dir>."""
    base = root / keymap_dir
    names = {f.name.split(".map")[0] for f in base.rglob("*.map*")} if base.is_dir() else set()
    return sorted(names)


def glibc_supported_locales(root: Path) -> List[str]:
    """Locale names listed in glibc's /usr/share/i18n/SUPPORTED."""
    supported = root / "usr/share/i18n/SUPPORTED"
    if not supported.is_file():
        return []
    return sorted({line.split()[0] for line in supported.read_text().splitlines() if line.strip() and not line.startswith("#")})


def glibc_source_locales(root: Path) -> List[str]:
    """<name>.UTF-8 for each locale source in /usr/share/i18n/locales (what localedef can compile)."""
    base = root / "usr/share/i18n/locales"
    if not base.is_dir():
        return []
    return sorted(f"{f.name}.UTF-8" for f in base.iterdir() if f.is_file() and "_" in f.name and not f.name.startswith("translit"))
