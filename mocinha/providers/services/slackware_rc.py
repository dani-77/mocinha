"""Slackware BSD-style init services (a77ien).

Slackware starts /etc/rc.d/rc.<name> at boot when the script is executable
(rc.M/rc.inet2 test `-x`); its setup's service menu (setup.services) enables
or disables a service by setting or clearing the execute bits, nothing else.
This provider does exactly that: chmod 755 to enable, chmod 644 to disable.

A service is available when /etc/rc.d/rc.<name> exists on the target.
Services the manifest does not mention keep the mode their package gave them.
"""

from pathlib import Path
from typing import List, Optional

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract


def rc_script(root: Path, name: str) -> Path:
    return root / "etc" / "rc.d" / f"rc.{name}"


def is_enabled(root: Path, name: str) -> bool:
    script = rc_script(root, name)
    return script.is_file() and bool(script.stat().st_mode & 0o111)


class SlackwareRcServiceProvider(ProviderContract):
    def __init__(self, name: str = "slackware-rc", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    @staticmethod
    def _to_disable(context: ExecutionContext) -> List[str]:
        return context.metadata.get("live_only_to_clean", []) + context.metadata.get("deselected_services", [])

    def validate(self, context: ExecutionContext) -> None:
        if context.metadata.get("default_target"):
            raise ExecutionError(
                message="[services].default_target is not supported by the Slackware rc provider.",
                cause="Slackware's default runlevel is set in /etc/inittab, not by a systemd-style target; "
                      "refusing instead of ignoring it.",
                failed_operation="Validate Slackware services",
                possible_recovery="Remove default_target from the manifest (declare /etc/inittab as a live or target file).",
            )

    def apply(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        enabled = context.metadata.get("enabled_services", [])
        to_disable = self._to_disable(context)
        missing = [s for s in enabled if not rc_script(root, s).is_file()]
        if missing:
            raise ExecutionError(
                message=f"Services without an rc script on the target: {missing}",
                cause=f"Slackware starts a service from /etc/rc.d/rc.<name>; none for {missing}.",
                failed_operation="Enable Slackware services",
                possible_recovery="Install the package that ships the rc script or drop the service from the manifest.",
            )
        for srv in to_disable:
            script = rc_script(root, srv)
            if script.is_file() and is_enabled(root, srv):
                self.events.action(EventPhase.CONFIGURE, f"Disabling service {srv} (chmod 644 /etc/rc.d/rc.{srv})")
                script.chmod(0o644)
        for srv in enabled:
            self.events.action(EventPhase.CONFIGURE, f"Enabling service {srv} (chmod 755 /etc/rc.d/rc.{srv})")
            rc_script(root, srv).chmod(0o755)

    def verify(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        enabled = context.metadata.get("enabled_services", [])
        to_disable = self._to_disable(context)
        problems = [f"rc.{s} is not executable" for s in enabled if not is_enabled(root, s)]
        problems += [f"rc.{s} is still executable" for s in to_disable if is_enabled(root, s)]
        if problems:
            raise VerificationError(message="Slackware services do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify Slackware services",
                                    possible_recovery="Check the modes of /etc/rc.d/rc.* on the target.")
        self.events.info(EventPhase.VERIFY, f"Slackware services verified: enabled {enabled}, disabled {to_disable}")
