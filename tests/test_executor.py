"""Unit tests for Plan Execution and Step Verification."""

import unittest

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.executor import InstallationExecutor
from mocinha.core.plan import InstallationPlan, PlanStep, TargetSummary
from mocinha.core.provider import ExecutionContext


class TestExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.executor = InstallationExecutor(self.stream)
        self.context = ExecutionContext(target_disk="/dev/mock0", target_mount="/tmp/mockmnt")

    def test_unconfirmed_execution_rejected(self) -> None:
        summary = TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="limine",
            init="systemd",
            services=["dbus"],
        )
        plan = InstallationPlan(summary=summary, steps=[])
        with self.assertRaises(ExecutionError) as ctx:
            self.executor.execute_plan(plan, self.context, confirmed=False)
        self.assertIn("explicit confirmation", str(ctx.exception))

    def test_confirmed_execution_runs_steps(self) -> None:
        run_log = []

        def step1_action(ctx: ExecutionContext) -> None:
            run_log.append("step1")

        def step2_action(ctx: ExecutionContext) -> None:
            run_log.append("step2")

        summary = TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="limine",
            init="systemd",
            services=["dbus"],
        )
        steps = [
            PlanStep("step1", "First Step", "Step 1 desc", True, "mock", execute_fn=step1_action, verify_fn=lambda c: None),
            PlanStep("step2", "Second Step", "Step 2 desc", True, "mock", execute_fn=step2_action, verify_fn=lambda c: None),
        ]
        plan = InstallationPlan(summary=summary, steps=steps)

        progress_records = []

        def progress_cb(current: int, total: int, step: PlanStep) -> None:
            progress_records.append((current, total, step.step_id))

        ok = self.executor.execute_plan(plan, self.context, confirmed=True, progress_cb=progress_cb)
        self.assertTrue(ok)
        self.assertEqual(run_log, ["step1", "step2"])
        self.assertEqual(len(progress_records), 2)
        self.assertEqual(progress_records[0], (1, 2, "step1"))
        self.assertEqual(progress_records[1], (2, 2, "step2"))

    def test_verification_failure_aborts_execution(self) -> None:
        def failing_verify(ctx: ExecutionContext) -> None:
            raise VerificationError(
                message="Expected ESP file /boot/EFI/BOOT/BOOTX64.EFI not found",
                cause="Bootloader installation did not write EFI binary",
                failed_operation="Verify EFI boot binary",
                current_state="File does not exist in target",
                possible_recovery="Check EFI mount and re-run bootloader install",
            )

        step = PlanStep(
            "bad_step",
            "Failing Verify Step",
            "Desc",
            True,
            "mock",
            verify_fn=failing_verify,
            verify_only=True,
        )
        summary = TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="limine",
            init="systemd",
            services=["dbus"],
        )
        plan = InstallationPlan(summary=summary, steps=[step])

        with self.assertRaises(ExecutionError) as ctx:
            self.executor.execute_plan(plan, self.context, confirmed=True)
        self.assertIn("Verification failed", str(ctx.exception))
        self.assertIn("Expected ESP file", str(ctx.exception))

    def test_provider_full_lifecycle_sequence(self) -> None:
        """Verifies strict adherence to probe -> validate -> prepare -> apply -> verify -> cleanup."""
        from mocinha.core.provider import ProviderContract
        from typing import List

        lifecycle_events: List[str] = []

        class MockLifecycleProvider(ProviderContract):
            def capabilities(self) -> List[str]:
                return ["mock"]

            def validate(self, context: ExecutionContext) -> None:
                lifecycle_events.append("validate")

            def prepare(self, context: ExecutionContext) -> None:
                lifecycle_events.append("prepare")

            def apply(self, context: ExecutionContext) -> None:
                lifecycle_events.append("apply")

            def verify(self, context: ExecutionContext) -> None:
                lifecycle_events.append("verify")

            def cleanup(self, context: ExecutionContext) -> None:
                lifecycle_events.append("cleanup")

        mock_prov = MockLifecycleProvider("mock-provider", self.stream)
        step = PlanStep(
            "mock_step",
            "Mock Lifecycle Step",
            "Desc",
            True,
            "mock-provider",
            provider=mock_prov,
            execute_fn=mock_prov.apply,
            verify_fn=mock_prov.verify,
        )
        summary = TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="limine",
            init="systemd",
            services=[],
        )
        plan = InstallationPlan(summary=summary, steps=[step], providers=[mock_prov])

        ok = self.executor.execute_plan(plan, self.context, confirmed=True)
        self.assertTrue(ok)
        self.assertEqual(lifecycle_events, ["validate", "prepare", "apply", "verify", "cleanup"])

    def test_cleanup_runs_even_on_step_failure(self) -> None:
        """Guarantees unmount and cleanup in finally block even if execution aborts."""
        from mocinha.core.provider import ProviderContract
        from typing import List

        lifecycle_events: List[str] = []

        class FailingProvider(ProviderContract):
            def capabilities(self) -> List[str]:
                return ["failing"]

            def validate(self, context: ExecutionContext) -> None:
                lifecycle_events.append("validate")

            def prepare(self, context: ExecutionContext) -> None:
                lifecycle_events.append("prepare")

            def apply(self, context: ExecutionContext) -> None:
                lifecycle_events.append("apply")
                raise RuntimeError("Disk write failed midway!")

            def verify(self, context: ExecutionContext) -> None:
                lifecycle_events.append("verify")

            def cleanup(self, context: ExecutionContext) -> None:
                lifecycle_events.append("cleanup")

        prov = FailingProvider("failing-provider", self.stream)
        step = PlanStep(
            "fail_step", "Failing Step", "Desc", True, "failing-provider",
            provider=prov, execute_fn=prov.apply, verify_fn=prov.verify,
        )
        summary = TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="limine",
            init="systemd",
            services=[],
        )
        plan = InstallationPlan(summary=summary, steps=[step], providers=[prov])

        with self.assertRaises(ExecutionError):
            self.executor.execute_plan(plan, self.context, confirmed=True)

        # validate -> prepare -> apply (fails) -> cleanup (guaranteed in finally)
        self.assertEqual(lifecycle_events, ["validate", "prepare", "apply", "cleanup"])

    def _summary(self) -> TargetSummary:
        return TargetSummary(
            disk="/dev/mock0",
            firmware="UEFI",
            partition_table="GPT",
            filesystem="ext4",
            bootloader="systemd-boot",
            init="systemd",
            services=[],
        )

    def test_unwired_step_rejected_before_anything_runs(self) -> None:
        """Regression: a step with no action used to be reported as 'Completed'."""
        run_log = []
        steps = [
            PlanStep("partition", "Partition", "Desc", True, "mock",
                     execute_fn=lambda c: run_log.append("partition"), verify_fn=lambda c: None),
            PlanStep("install_bootloader", "Install bootloader", "Desc", True, "systemd-boot"),
        ]
        plan = InstallationPlan(summary=self._summary(), steps=steps)
        with self.assertRaises(ExecutionError) as ctx:
            self.executor.execute_plan(plan, self.context, confirmed=True)
        self.assertIn("install_bootloader", str(ctx.exception))
        self.assertEqual(run_log, [])

    def test_step_without_verification_rejected(self) -> None:
        steps = [PlanStep("partition", "Partition", "Desc", True, "mock", execute_fn=lambda c: None)]
        plan = InstallationPlan(summary=self._summary(), steps=steps)
        with self.assertRaises(ExecutionError) as ctx:
            self.executor.execute_plan(plan, self.context, confirmed=True)
        self.assertIn("has no verification", str(ctx.exception))

    def test_provider_only_step_does_not_fall_back_to_provider_methods(self) -> None:
        """Regression: an unbound step used to call provider.apply()/verify() implicitly."""
        from mocinha.core.provider import ProviderContract

        calls = []

        class Prov(ProviderContract):
            def capabilities(self):
                return ["mock"]

            def validate(self, context):
                calls.append("validate")

            def apply(self, context):
                calls.append("apply")

            def verify(self, context):
                calls.append("verify")

        prov = Prov("mock", self.stream)
        plan = InstallationPlan(
            summary=self._summary(),
            steps=[PlanStep("target_mount", "Mount", "Desc", False, "mock", provider=prov)],
            providers=[prov],
        )
        with self.assertRaises(ExecutionError):
            self.executor.execute_plan(plan, self.context, confirmed=True)
        self.assertEqual(calls, [])

    def test_cleanup_failure_is_reported_as_warning(self) -> None:
        """Regression: EventStream had no warning(), so a failing cleanup crashed the executor."""
        from mocinha.core.events import EventLevel
        from mocinha.core.provider import ProviderContract

        class BadCleanup(ProviderContract):
            def capabilities(self):
                return ["mock"]

            def validate(self, context):
                pass

            def apply(self, context):
                pass

            def verify(self, context):
                pass

            def cleanup(self, context):
                raise RuntimeError("umount busy")

        prov = BadCleanup("bad-cleanup", self.stream)
        plan = InstallationPlan(
            summary=self._summary(),
            steps=[PlanStep("s", "S", "Desc", False, "bad-cleanup", provider=prov,
                            execute_fn=prov.apply, verify_fn=prov.verify)],
            providers=[prov],
        )
        self.assertTrue(self.executor.execute_plan(plan, self.context, confirmed=True))
        warnings = [e for e in self.stream.history if e.level == EventLevel.WARNING]
        self.assertTrue(any("umount busy" in e.message for e in warnings))


if __name__ == "__main__":
    unittest.main()

