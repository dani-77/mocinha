"""Unit tests for plan wiring (wire_plan_providers) against the example manifests."""

from pathlib import Path
import unittest

from mocinha.core.errors import ResolutionError
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry, wire_plan_providers

MANIFESTS = Path(__file__).resolve().parent.parent / "examples" / "manifests"


def resolve(manifest_name: str, bootloader: str, firmware: FirmwareType, platform: str = "linux", extra_boot=()):
    manifest = Manifest.load_from_file(MANIFESTS / f"{manifest_name}.toml")
    manifest.boot.available.extend(extra_boot)
    facts = SystemFacts(platform, "x86_64", firmware, [DiskDevice("/dev/vda", 20 * 1024**3)], [])
    registry = create_default_registry()
    plan = InstallationResolver(facts, manifest, registry).resolve(
        UserChoices(hostname="test-host", target_disk="/dev/vda", bootloader=bootloader, username="dani", password="x")
    )
    return plan, registry, manifest


class TestWiring(unittest.TestCase):
    def test_btw_d77_grub_plan_fully_wired(self) -> None:
        plan, registry, manifest = resolve("btw-d77", "grub", FirmwareType.BIOS)
        wire_plan_providers(plan, registry, manifest)
        for step in plan.steps:
            self.assertIsNotNone(step.verify_fn, step.step_id)
            if not step.verify_only:
                self.assertIsNotNone(step.execute_fn, step.step_id)

    def test_mount_and_unmount_steps_have_their_own_verification(self) -> None:
        """Regression: target_mount/target_unmount fell back to the fstab check and always failed."""
        plan, registry, manifest = resolve("btw-d77", "grub", FirmwareType.BIOS)
        wire_plan_providers(plan, registry, manifest)
        steps = {s.step_id: s for s in plan.steps}
        self.assertEqual(steps["target_mount"].verify_fn.__name__, "verify_mounted")
        self.assertEqual(steps["configure_fstab"].verify_fn.__name__, "verify_fstab")
        self.assertEqual(steps["target_unmount"].verify_fn.__name__, "verify_unmounted")

    def test_no_noop_live_only_step(self) -> None:
        """Regression: 'Remove live-only components' was marked destructive but did nothing."""
        plan, _, _ = resolve("btw-d77", "grub", FirmwareType.BIOS)
        self.assertNotIn("cleanup_live_only", [s.step_id for s in plan.steps])

    def test_bootloader_without_provider_rejected(self) -> None:
        """Regression: the old btw-d77 manifest declared systemd-boot, which has no provider."""
        plan, registry, manifest = resolve("btw-d77", "systemd-boot", FirmwareType.UEFI, extra_boot=["systemd-boot"])
        with self.assertRaises(ResolutionError) as ctx:
            wire_plan_providers(plan, registry, manifest)
        self.assertIn("systemd-boot", str(ctx.exception))

    def test_lilo_without_provider_rejected(self) -> None:
        plan, registry, manifest = resolve("sysvd77", "lilo", FirmwareType.BIOS, extra_boot=["lilo"])
        with self.assertRaises(ResolutionError):
            wire_plan_providers(plan, registry, manifest)

    def test_unknown_step_rejected(self) -> None:
        from mocinha.core.plan import PlanStep

        plan, registry, manifest = resolve("btw-d77", "grub", FirmwareType.BIOS)
        plan.steps.append(PlanStep("mystery_step", "Mystery", "Desc", True, "nobody"))
        with self.assertRaises(ResolutionError) as ctx:
            wire_plan_providers(plan, registry, manifest)
        self.assertIn("mystery_step", str(ctx.exception))

    def test_misspelled_manifest_provider_rejected(self) -> None:
        plan, registry, manifest = resolve("btw-d77", "grub", FirmwareType.BIOS)
        manifest.providers.services = "arch-systemdd"
        with self.assertRaises(ResolutionError) as ctx:
            wire_plan_providers(plan, registry, manifest)
        self.assertIn("arch-systemdd", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
