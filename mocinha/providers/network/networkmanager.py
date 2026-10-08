"""Network connection through NetworkManager (nmcli)."""

from typing import Any, Dict, List, Optional
import re
import shutil

from mocinha.core.errors import ExecutionError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner
from mocinha.providers.network import NetworkStatus, WifiNetwork


def split_terse(line: str) -> List[str]:
    """nmcli -t output: ':'-separated, with ':' and '\\' escaped by '\\'."""
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]


class NetworkManagerProvider(ProviderContract):
    def __init__(self, name: str = "networkmanager", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["network"]

    def _nmcli(self, *args: str, check: bool = True, input_text: Optional[str] = None):
        return self.runner.run(["nmcli", *args], phase=EventPhase.PROBE, check=check, input_text=input_text)

    def probe(self) -> Dict[str, Any]:
        if not shutil.which("nmcli"):
            return {"active": False}
        proc = self._nmcli("-t", "-f", "RUNNING", "general", check=False)
        return {"active": proc.returncode == 0 and proc.stdout.strip() == "running"}

    def status(self) -> NetworkStatus:
        general = split_terse(self._nmcli("-t", "-f", "STATE,CONNECTIVITY", "general").stdout.strip())
        active = [split_terse(l) for l in self._nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active").stdout.splitlines() if l]
        devices = [split_terse(l) for l in self._nmcli("-t", "-f", "DEVICE,TYPE", "device").stdout.splitlines() if l]
        state, connectivity = (general + ["", ""])[:2]
        return NetworkStatus(
            stack=self.name, connected=connectivity == "full",
            detail=f"NetworkManager: {state}, connectivity {connectivity}",
            connections=[f"{a[0]} ({a[1]} on {a[2]})" for a in active if len(a) >= 3],
            wifi_devices=[d[0] for d in devices if len(d) >= 2 and d[1] == "wifi"],
        )

    def scan(self) -> List[WifiNetwork]:
        out = self._nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes").stdout
        networks: Dict[str, WifiNetwork] = {}
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) < 4 or not f[1]:
                continue
            net = WifiNetwork(ssid=f[1], signal=int(f[2]) if f[2].isdigit() else None,
                              security=f[3] or "open", connected=f[0] == "*")
            best = networks.get(net.ssid)
            if best is None or (net.signal or 0) > (best.signal or 0) or net.connected:
                networks[net.ssid] = net
        return sorted(networks.values(), key=lambda n: (not n.connected, -(n.signal or 0)))

    def connect(self, ssid: str, password: Optional[str] = None, device: Optional[str] = None) -> NetworkStatus:
        # --ask reads the secret from stdin: it never appears on a command line or in the log
        cmd = ["--ask", "device", "wifi", "connect", ssid] + (["ifname", device] if device else [])
        proc = self._nmcli(*cmd, check=False, input_text=(password or "") + "\n")
        if proc.returncode != 0:
            raise ExecutionError(
                message=f"Could not connect to {ssid!r}.",
                cause=(proc.stderr or proc.stdout).strip(),
                failed_operation="Connect to Wi-Fi (NetworkManager)",
                possible_recovery="Check the password and the signal, then try again.",
            )
        return self.status()

    # Network providers act on the live only and are never steps of an installation plan
    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        raise ExecutionError(message="Network providers are not installation steps.", cause="Wiring error.",
                             failed_operation="Apply network provider")

    def verify(self, context: ExecutionContext) -> None:
        self.apply(context)
