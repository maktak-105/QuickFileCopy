"""Network Neighborhood browsing via the Windows shell namespace: walks the
same virtual "Network" folder Explorer's nav pane shows, so this surfaces
exactly the computers/shares Explorer would - including host discovery that
has no simple Win32 API equivalent (SSDP/WS-Discovery based, wired up
through the shell's Network Explorer folder rather than the legacy
NetServerEnum/Computer Browser service, which is unreliable on modern LANs).
"""
from __future__ import annotations

import os

from win32com.client import Dispatch
from win32com.shell import shell, shellcon

# CLSID of the shell's "Network" virtual folder (same one Explorer's nav
# pane shows as "ネットワーク" / "Network").
_NETWORK_GUID = "::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}"


def _bind(parsing_name: str):
    desktop = shell.SHGetDesktopFolder()
    _, pidl, _ = desktop.ParseDisplayName(0, None, parsing_name)
    return desktop.BindToObject(pidl, None, shell.IID_IShellFolder)


def list_network_computers() -> list[tuple[str, str]]:
    """[(display_name, '\\\\HOST'), ...] for SMB computers visible under
    Explorer's "ネットワーク" node.

    The shell's Network folder also lists every SSDP/UPnP device on the LAN
    (routers, TVs, media renderers) as non-browsable entries; those have a
    provider-GUID "parsing name" instead of a UNC path, so keeping only
    entries whose parsing name starts with "\\\\" filters down to real,
    browsable file servers - matching what Explorer's own icons show.
    """
    try:
        network_folder = _bind(_NETWORK_GUID)
        enum = network_folder.EnumObjects(
            0, shellcon.SHCONTF_FOLDERS | shellcon.SHCONTF_NONFOLDERS
        )
        seen: set[str] = set()
        entries: list[tuple[str, str]] = []
        for child_pidl in enum:
            parsing = network_folder.GetDisplayNameOf(child_pidl, shellcon.SHGDN_FORPARSING)
            if not parsing.startswith("\\\\"):
                continue
            key = parsing.upper()
            if key in seen:
                continue
            seen.add(key)
            name = network_folder.GetDisplayNameOf(child_pidl, shellcon.SHGDN_NORMAL)
            entries.append((name, parsing))
        entries.sort(key=lambda e: e[0].lower())
        return entries
    except Exception:
        return []


def list_network_locations() -> list[tuple[str, str]]:
    """[(display_name, target_path), ...] for "network locations" - UNC
    shortcuts added via Explorer's "Add a network location" wizard, which
    Explorer lists under "This PC" alongside drives even though they have
    no drive letter. These live as one subfolder per location under the
    NetHood shell folder, each holding a target.lnk resolving to the UNC
    path; resolving via IShellLink (the .lnk COM interface) rather than
    the shell namespace since NetHood link-folders don't resolve their
    target through GetDisplayNameOf the way ordinary .lnk files do.
    """
    try:
        nethood = shell.SHGetFolderPath(0, shellcon.CSIDL_NETHOOD, 0, 0)
    except Exception:
        return []
    if not os.path.isdir(nethood):
        return []

    entries: list[tuple[str, str]] = []
    try:
        wsh = Dispatch("WScript.Shell")
    except Exception:
        return []
    for sub in os.scandir(nethood):
        if not sub.is_dir():
            continue
        lnk = os.path.join(sub.path, "target.lnk")
        if not os.path.isfile(lnk):
            continue
        try:
            target = wsh.CreateShortcut(lnk).TargetPath
        except Exception:
            continue
        if target:
            entries.append((sub.name, target))
    entries.sort(key=lambda e: e[0].lower())
    return entries


def list_shares(computer_path: str) -> list[tuple[str, str]]:
    """[(display_name, '\\\\HOST\\Share'), ...] for a computer's shared
    folders (printers and other non-folder shares are excluded).
    """
    try:
        computer_folder = _bind(computer_path)
        enum = computer_folder.EnumObjects(0, shellcon.SHCONTF_FOLDERS)
        entries = []
        for child_pidl in enum:
            parsing = computer_folder.GetDisplayNameOf(child_pidl, shellcon.SHGDN_FORPARSING)
            name = computer_folder.GetDisplayNameOf(child_pidl, shellcon.SHGDN_INFOLDER)
            entries.append((name, parsing))
        entries.sort(key=lambda e: e[0].lower())
        return entries
    except Exception:
        return []
