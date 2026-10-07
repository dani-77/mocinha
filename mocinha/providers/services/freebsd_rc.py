"""FreeBSD rc.d / rc.conf service management provider (au-d77).

Configures persistent services using sysrc -R <target_mount> <service>_enable="YES"
and verifies persistence directly in the target /etc/rc.conf.
"""

from pathlib import Path
from typing import List, Optional

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDServiceProvider(ProviderContract):
    """FreeBSD rc.d service manager configuring /etc/rc.conf."""

    def __init__(self, name: str = "freebsd-rc", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    def validate(self, context: ExecutionContext) -> None:
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        for srv in enabled_services:
            if not isinstance(srv, str) or not srv.strip():
                raise VerificationError(
                    message=f"Invalid FreeBSD rc service identifier: '{srv}'",
                    cause="Service names must be non-empty strings.",
                    failed_operation="Validate FreeBSD rc services",
                )

    def apply(self, context: ExecutionContext) -> None:
        target_root = context.target_mount
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        live_only: List[str] = context.metadata.get("live_only_to_clean", [])

        import shutil

        # 1. Clean live-only services from target /etc/rc.conf
        for srv in live_only:
            var_name = f"{srv}_enable"
            self.events.info(EventPhase.CONFIGURE, f"Disabling live-only service on target: {srv}")
            if shutil.which("sysrc"):
                self.runner.run(
                    ["sysrc", "-R", target_root, f"{var_name}=NO"],
                    phase=EventPhase.CONFIGURE,
                    check=False,
                )

        # 2. Enable requested persistent services
        for srv in enabled_services:
            var_name = f"{srv}_enable"
            self.events.info(EventPhase.CONFIGURE, f"Enabling persistent FreeBSD service: {srv}")
            if shutil.which("sysrc"):
                self.runner.run(
                    ["sysrc", "-R", target_root, f"{var_name}=YES"],
                    phase=EventPhase.CONFIGURE,
                    check=False,
                )
            # Direct ensure in /etc/rc.conf
            rc_conf = Path(target_root) / "etc" / "rc.conf"
            rc_conf.parent.mkdir(parents=True, exist_ok=True)
            existing = rc_conf.read_text() if rc_conf.is_file() else ""
            if f'{var_name}="YES"' not in existing and f"{var_name}=YES" not in existing:
                with open(rc_conf, "a") as f:
                    f.write(f'{var_name}="YES"\n')

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        rc_conf = target_root / "etc" / "rc.conf"

        if not rc_conf.is_file():
            raise VerificationError(
                message="Target /etc/rc.conf file not found.",
                cause="Target filesystem lacks /etc/rc.conf after service configuration.",
                failed_operation="Verify FreeBSD rc.conf",
                current_state=f"{rc_conf} does not exist",
                possible_recovery="Verify root filesystem deployment and sysrc execution.",
            )

        content = rc_conf.read_text()
        missing = []
        for srv in enabled_services:
            var_pattern1 = f'{srv}_enable="YES"'
            var_pattern2 = f'{srv}_enable=YES'
            var_pattern3 = f'{srv}_enable="yes"'
            if var_pattern1 not in content and var_pattern2 not in content and var_pattern3 not in content:
                missing.append(srv)

        if missing:
            raise VerificationError(
                message=f"Target verification failed: services missing in /etc/rc.conf: {missing}",
                cause="sysrc or rc.conf append failed to register required service enable variables.",
                failed_operation="Verify FreeBSD service persistence",
                current_state=f"Missing in rc.conf: {missing}",
                possible_recovery="Inspect /etc/rc.conf on target.",
            )
        self.events.info(EventPhase.VERIFY, "FreeBSD rc.conf services successfully verified on target.")
