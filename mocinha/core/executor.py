"""Plan execution engine.

Executes an approved, validated installation plan step-by-step.
Maintains transparency via event streaming and enforces explicit confirmation.
"""

from typing import Callable, Optional
import time

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventLevel, EventPhase, EventStream
from mocinha.core.plan import InstallationPlan, PlanStep
from mocinha.core.provider import ExecutionContext


ProgressCallback = Callable[[int, int, PlanStep], None]


class InstallationExecutor:
    """Executes validated installation steps sequentially."""

    def __init__(self, event_stream: Optional[EventStream] = None) -> None:
        self.events = event_stream or EventStream()

    def execute_plan(
        self,
        plan: InstallationPlan,
        context: ExecutionContext,
        confirmed: bool = False,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> bool:
        if not confirmed:
            raise ExecutionError(
                message="Cannot execute plan without explicit confirmation.",
                cause="The user or caller did not explicitly confirm destructive execution.",
                failed_operation="Execute installation plan",
                current_state="Confirmation flag is False.",
                possible_recovery="Present plan to user and require explicit confirmation.",
            )

        total_steps = len(plan.steps)
        self.events.info(
            EventPhase.PREPARE,
            f"Execution requested for target disk {plan.summary.disk} ({total_steps} steps planned)."
        )

        # 1. Identify distinct active providers participating in the plan
        active_providers = []
        seen_names = set()
        for prov in getattr(plan, "providers", []):
            if prov and getattr(prov, "name", None) not in seen_names:
                active_providers.append(prov)
                seen_names.add(prov.name)
        for step in plan.steps:
            p = getattr(step, "provider", None)
            if p and getattr(p, "name", None) not in seen_names:
                active_providers.append(p)
                seen_names.add(p.name)

        # 2. LIFECYCLE PHASE: VALIDATE (Pre-flight checks before ANY disk action)
        self.events.info(EventPhase.PREPARE, f"Running pre-flight validation across {len(active_providers)} active provider(s)...")
        for prov in active_providers:
            self.events.info(EventPhase.PREPARE, f"Validating provider '{prov.name}' requirements...")
            prov.validate(context)

        self.events.info(
            EventPhase.PREPARE,
            f"Pre-flight validation passed. Starting execution on target disk {plan.summary.disk}."
        )

        try:
            # 3. LIFECYCLE PHASE: PREPARE (Setup staging, locks, mountpoints)
            self.events.info(EventPhase.PREPARE, "Executing provider preparation phase...")
            for prov in active_providers:
                prov.prepare(context)

            # 4. LIFECYCLE PHASE: APPLY & VERIFY (Step-by-step)
            for idx, step in enumerate(plan.steps, start=1):
                if progress_cb:
                    progress_cb(idx, total_steps, step)

                phase = EventPhase.DEPLOY if "deploy" in step.step_id else EventPhase.CONFIGURE
                self.events.action(
                    phase,
                    f"Step {idx}/{total_steps}: {step.title}",
                )

                start_t = time.time()
                try:
                    # Apply
                    if step.execute_fn:
                        step.execute_fn(context)
                    elif getattr(step, "provider", None):
                        step.provider.apply(context)

                    # Verify
                    if step.verify_fn:
                        self.events.info(phase, f"Verifying target state for step: {step.title}")
                        step.verify_fn(context)
                    elif getattr(step, "provider", None) and hasattr(step.provider, "verify"):
                        self.events.info(phase, f"Verifying target state for step: {step.title}")
                        step.provider.verify(context)

                    duration = round(time.time() - start_t, 2)
                    self.events.info(
                        phase,
                        f"Completed step {idx}/{total_steps}: {step.title} in {duration}s."
                    )

                except VerificationError as ve:
                    self.events.error(
                        phase,
                        f"Verification failed on step {step.title}: {ve}",
                    )
                    raise ExecutionError(
                        message=f"Verification failed on step '{step.title}': {ve.message}",
                        cause=ve.cause,
                        failed_operation=f"Verify step {step.step_id}",
                        current_state=f"Step failed after action: {ve.current_state}",
                        possible_recovery=ve.possible_recovery,
                    ) from ve

                except Exception as e:
                    self.events.error(
                        phase,
                        f"Execution failed on step {step.title}: {e}",
                    )
                    raise ExecutionError(
                        message=f"Failed to execute step '{step.title}': {e}",
                        cause=str(e),
                        failed_operation=f"Execute {step.step_id}",
                        current_state=f"Step failed at {idx}/{total_steps}",
                        possible_recovery="Inspect target mount and event logs.",
                    ) from e

            self.events.info(EventPhase.VERIFY, "All installation steps completed and verified successfully.")
            return True

        finally:
            # 5. LIFECYCLE PHASE: CLEANUP (Guaranteed to execute on success or failure)
            self.events.info(EventPhase.CLEANUP, "Executing provider cleanup phase...")
            for prov in reversed(active_providers):
                try:
                    prov.cleanup(context)
                except Exception as ce:
                    self.events.warning(EventPhase.CLEANUP, f"Cleanup warning in provider '{prov.name}': {ce}")
