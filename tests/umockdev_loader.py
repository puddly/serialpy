"""Replay umockdev-record dumps into a fake sysfs/devfs path."""

from __future__ import annotations

import os
from pathlib import Path

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}


def _decode_attr(raw: str) -> str:
    """Decode umockdev attribute escapes."""
    out: list[str] = []
    i = 0

    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw) and raw[i + 1] in _ESCAPES:
            out.append(_ESCAPES[raw[i + 1]])
            i += 2
        else:
            out.append(raw[i])
            i += 1

    return "".join(out)


def load_umockdev(tmp_path: Path, dump: Path) -> tuple[Path, Path]:
    """Replay a `.umockdev` dump under `tmp_path`. Returns (sys_root, dev_root)."""
    sys_root = tmp_path / "sys"
    dev_root = tmp_path / "dev"
    sys_root.mkdir(parents=True, exist_ok=True)
    dev_root.mkdir(parents=True, exist_ok=True)

    paragraphs = [p for p in dump.read_text().split("\n\n") if p.strip()]

    for paragraph in paragraphs:
        devpath: str | None = None
        subsystem: str | None = None
        devnode: str | None = None
        attrs: dict[str, str] = {}
        bin_attrs: dict[str, bytes] = {}
        symlinks: dict[str, str] = {}
        dev_aliases: list[str] = []

        for line in paragraph.splitlines():
            if line.startswith("P: "):
                devpath = line[3:].lstrip("/")
            elif line.startswith("N: "):
                # `N: name` or `N: name=<hex contents>`; we don't need the contents.
                devnode = line[3:].split("=", 1)[0]
            elif line.startswith("S: "):
                dev_aliases.append(line[3:])
            elif line.startswith("E: "):
                key, _, val = line[3:].partition("=")
                if key == "SUBSYSTEM":
                    subsystem = val
            elif line.startswith("A: "):
                key, _, val = line[3:].partition("=")
                attrs[key] = _decode_attr(val)
            elif line.startswith("H: "):
                key, _, val = line[3:].partition("=")
                bin_attrs[key] = bytes.fromhex(val)
            elif line.startswith("L: "):
                key, _, target = line[3:].partition("=")
                symlinks[key] = target

        if devpath is None:
            continue

        device_dir = sys_root / devpath
        device_dir.mkdir(parents=True, exist_ok=True)

        # tty is a class; everything else (usb, usb-serial, platform, pnp, serial-base,
        # amba, pci, ...) is a bus
        if subsystem:
            container = "class" if subsystem == "tty" else "bus"
            subsystem_dir = sys_root / container / subsystem
            subsystem_dir.mkdir(parents=True, exist_ok=True)

            subsys_link = device_dir / "subsystem"
            if not subsys_link.is_symlink():
                subsys_link.symlink_to(os.path.relpath(subsystem_dir, device_dir))

            class_link = subsystem_dir / Path(devpath).name
            if not class_link.is_symlink():
                class_link.symlink_to(os.path.relpath(device_dir, class_link.parent))

        for key, val in attrs.items():
            f = device_dir / key
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(val)

        for key, bin_val in bin_attrs.items():
            f = device_dir / key
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(bin_val)

        for key, target in symlinks.items():
            link = device_dir / key
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.is_symlink():
                # Pre-create the resolved target so Path.exists() returns True
                resolved = Path(os.path.normpath(link.parent / target))
                if str(resolved).startswith(str(sys_root)):
                    resolved.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)

        if devnode:
            node = dev_root / devnode
            node.parent.mkdir(parents=True, exist_ok=True)
            if not node.exists():
                node.touch()
            for alias in dev_aliases:
                link = dev_root / alias
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink() and not link.exists():
                    link.symlink_to(os.path.relpath(node, link.parent))

    return sys_root, dev_root
