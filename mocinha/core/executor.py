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
            f"Execution started for target disk {plan.summary.disk} ({total_steps} steps planned)."
        )

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
                # 1. Execute action
                if step.execute_fn:
                    step.execute_fn(context)

                # 2. Verify step if verification callback defined
                if step.verify_fn:
                    self.events.info(phase, f"Verifying target state for step: {step.title}")
                    step.verify_fn(context)

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
