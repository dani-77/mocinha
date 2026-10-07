"""Unit tests for the Service Subsystem, dependency graph, conflicts, cycles, and live-only rules."""

import unittest

from mocinha.core.errors import ResolutionError
from mocinha.core.services import ServiceCategory, ServiceGraph, ServiceItem


class TestServices(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = ServiceGraph()
        self.graph.register_service(
            ServiceItem(id="dbus", category=ServiceCategory.REQUIRED)
        )
        self.graph.register_service(
            ServiceItem(
                id="NetworkManager",
                category=ServiceCategory.DEFAULT_ENABLED,
                requires=["dbus"],
                conflicts=["systemd-networkd"],
            )
        )
        self.graph.register_service(
            ServiceItem(
                id="systemd-networkd",
                category=ServiceCategory.OPTIONAL,
                conflicts=["NetworkManager"],
            )
        )
        self.graph.register_service(
            ServiceItem(
                id="sshd",
                category=ServiceCategory.OPTIONAL,
            )
        )
        self.graph.register_service(
            ServiceItem(
                id="live-setup",
                category=ServiceCategory.LIVE_ONLY,
            )
        )
        self.graph.register_service(
            ServiceItem(
                id="bluetooth-daemon",
                category=ServiceCategory.OPTIONAL,
            )
        )
        self.graph.register_service(
            ServiceItem(
                id="bluetooth-applet",
                category=ServiceCategory.OPTIONAL,
                requires=["bluetooth-daemon"],
            )
        )

    def test_required_services_always_included(self) -> None:
        # User requested empty set; dbus MUST still be included because it is REQUIRED
        res = self.graph.resolve_service_graph(set())
        self.assertIn("dbus", res.enabled_services)

    def test_automatic_dependency_inclusion(self) -> None:
        # Request bluetooth-applet without specifying bluetooth-daemon
        res = self.graph.resolve_service_graph({"bluetooth-applet"})
        self.assertIn("bluetooth-daemon", res.enabled_services)
        self.assertIn("bluetooth-applet", res.enabled_services)
        self.assertEqual(res.auto_included_dependencies.get("bluetooth-daemon"), "bluetooth-applet")

    def test_service_conflict_raises_diagnostic_error(self) -> None:
        # Requesting conflicting services
        with self.assertRaises(ResolutionError) as ctx:
            self.graph.resolve_service_graph({"NetworkManager", "systemd-networkd"})
        self.assertIn("Service conflict detected", str(ctx.exception))
        self.assertIn("NetworkManager", str(ctx.exception))
        self.assertIn("systemd-networkd", str(ctx.exception))

    def test_cycle_detection(self) -> None:
        cyclic_graph = ServiceGraph()
        cyclic_graph.register_service(
            ServiceItem(id="srvA", category=ServiceCategory.OPTIONAL, requires=["srvB"])
        )
        cyclic_graph.register_service(
            ServiceItem(id="srvB", category=ServiceCategory.OPTIONAL, requires=["srvA"])
        )
        with self.assertRaises(ResolutionError) as ctx:
            cyclic_graph.resolve_service_graph({"srvA"})
        self.assertIn("cycle detected", str(ctx.exception).lower())

    def test_live_only_never_persisted(self) -> None:
        res = self.graph.resolve_service_graph({"live-setup", "sshd"})
        self.assertNotIn("live-setup", res.enabled_services)
        self.assertIn("sshd", res.enabled_services)
        self.assertIn("live-setup", res.live_only_to_clean)

    def test_unselected_services_are_disabled_on_target(self) -> None:
        """Regression (btw-d77): services enabled in the live but not chosen stayed enabled."""
        result = self.graph.resolve_service_graph(set())
        selected = set(result.enabled_services)
        live_only = set(result.live_only_to_clean)
        self.assertTrue(result.deselected)
        for s_id in result.deselected:
            self.assertNotIn(s_id, selected)
            self.assertNotIn(s_id, live_only)
        known = set(self.graph.services)
        self.assertEqual(selected | live_only | set(result.deselected), known)


if __name__ == "__main__":
    unittest.main()
