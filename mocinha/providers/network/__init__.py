"""Network connection providers (AGENTS.md "Online rules", rule 7).

Frontends use these to show the connection state and connect to a network
before an install with online components. Each provider drives one native
network stack of the live; the one the live actually runs is selected
(probe()["active"]), never assumed from the distribution. Credentials go to
the live's own network stack only; nothing is written to the target.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WifiNetwork:
    ssid: str
    signal: Optional[int] = None     # percent, when the stack reports it
    security: str = ""               # e.g. "WPA2", "open"
    connected: bool = False


@dataclass
class NetworkStatus:
    stack: str                       # provider name
    connected: bool                  # the stack reports full connectivity
    detail: str                      # human-readable state
    connections: List[str] = field(default_factory=list)  # active connections/devices
    wifi_devices: List[str] = field(default_factory=list)


def select_network_provider(registry) -> Optional[object]:
    """The provider whose network stack runs on this live, or None."""
    for provider in registry.list_capability("network"):
        try:
            if provider.probe().get("active"):
                return provider
        except Exception:  # a broken stack is simply not selected
            continue
    return None
