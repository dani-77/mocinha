"""Network connection through iwd (iwctl) --- the archiso/btw-d77 live stack.

iwd only manages Wi-Fi; wired links on such lives are configured by
systemd-networkd/DHCP on their own, so the connection state also reports
whether the live has a default route.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import re
import shutil

from mocinha.core.errors import ExecutionError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner
from mocinha.providers.network import NetworkStatus, WifiNetwork

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
IWD_DIR = Path("/var/lib/iwd")


def table_rows(text: str) -> List[str]:
    """Data rows of an iwctl table (after the header separator lines)."""
    lines = [ANSI.sub("", l).rstrip() for l in text.splitlines()]
    seps = [i for i, l in enumerate(lines) if l.strip() and set(l.strip()) <= {"-"}]
    if len(seps) < 2:
        return []  # no table, e.g. "No devices in Station mode available."
    return [l for l in lines[seps[1] + 1:] if l.strip() and not l.strip().startswith("No ")]


def psk_file_name(ssid: str) -> str:
    """iwd's network configuration file name for a PSK network."""
    if re.fullmatch(r"[A-Za-z0-9_ -]+", ssid):
        return f"{ssid}.psk"
    return "=" + ssid.encode().hex() + ".psk"


def has_default_route(proc_root: Path = Path("/proc")) -> bool:
    route = proc_root / "net" / "route"
    if not route.is_file():
        return False
    for line in route.read_text().splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000":
            return True
    return False


class IwdProvider(ProviderContract):
    def __init__(self, name: str = "iwd", event_stream: Optional[EventStream] = None,
                 iwd_dir: Path = IWD_DIR) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)
        self.iwd_dir = iwd_dir

    def capabilities(self) -> List[str]:
        return ["network"]

    def _iwctl(self, *args: str, check: bool = True):
        return self.runner.run(["iwctl", *args], phase=EventPhase.PROBE, check=check)

    def probe(self) -> Dict[str, Any]:
        if not shutil.which("iwctl"):
            return {"active": False}
        return {"active": self._iwctl("station", "list", check=False).returncode == 0}

    def _stations(self) -> List[str]:
        return [r.split()[0] for r in table_rows(self._iwctl("station", "list").stdout) if r.split()]

    def status(self) -> NetworkStatus:
        stations = self._stations()
        connected = []
        for dev in stations:
            show = ANSI.sub("", self._iwctl("station", dev, "show").stdout)
            state = re.search(r"State\s+(\S+)", show)
            net = re.search(r"Connected network\s+(.+?)\s*$", show, re.M)
            if state and state.group(1) == "connected":
                connected.append(f"{net.group(1) if net else '?'} (wifi on {dev})")
        route = has_default_route()
        return NetworkStatus(
            stack=self.name, connected=route,
            detail=f"iwd: {len(connected)} Wi-Fi connection(s); default route: {'yes' if route else 'no'}",
            connections=connected, wifi_devices=stations,
        )

    def scan(self) -> List[WifiNetwork]:
        result = []
        for dev in self._stations():
            self._iwctl("station", dev, "scan", check=False)
            for row in table_rows(self._iwctl("station", dev, "get-networks").stdout):
                connected = row.lstrip().startswith(">")
                parts = re.split(r"\s{2,}", row.strip().lstrip(">").strip())
                if len(parts) >= 2:
                    result.append(WifiNetwork(ssid=parts[0], security=parts[1],
                                              signal=parts[2].count("*") * 25 if len(parts) > 2 else None,
                                              connected=connected))
        return result

    def connect(self, ssid: str, password: Optional[str] = None, device: Optional[str] = None) -> NetworkStatus:
        stations = self._stations()
        dev = device or (stations[0] if stations else None)
        if dev is None:
            raise ExecutionError(message="No Wi-Fi device managed by iwd.", cause="iwctl station list is empty.",
                                 failed_operation="Connect to Wi-Fi (iwd)")
        if password:
            # iwd reads the passphrase from its network file; it never appears on a command line or in the log
            self.iwd_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            psk = self.iwd_dir / psk_file_name(ssid)
            psk.write_text(f"[Security]\nPassphrase={password}\n")
            psk.chmod(0o600)
        proc = self._iwctl("station", dev, "connect", ssid, check=False)
        if proc.returncode != 0:
            raise ExecutionError(message=f"Could not connect to {ssid!r}.", cause=ANSI.sub("", proc.stdout + proc.stderr).strip(),
                                 failed_operation="Connect to Wi-Fi (iwd)",
                                 possible_recovery="Check the password and the signal, then try again.")
        return self.status()

    # Network providers act on the live only and are never steps of an installation plan
    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        raise ExecutionError(message="Network providers are not installation steps.", cause="Wiring error.",
                             failed_operation="Apply network provider")

    def verify(self, context: ExecutionContext) -> None:
        self.apply(context)
