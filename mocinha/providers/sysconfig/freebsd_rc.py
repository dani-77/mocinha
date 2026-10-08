"""System settings on FreeBSD: hostname in /etc/rc.conf (au-d77).

Locale, keymap and timezone changes are not implemented yet and are refused
during validation, so the live's settings are kept.
"""

from pathlib import Path
from typing import List, Optional

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract


class FreeBSDRcSysconfigProvider(ProviderContract):
    def __init__(self, name: str = "freebsd-rc", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    def validate(self, context: ExecutionContext) -> None:
        if any(context.metadata.get(k) for k in ("locale", "keymap", "timezone")):
            raise ExecutionError(
                message="Locale, keymap and timezone configuration is not implemented for FreeBSD.",
                cause="The plan asks to change them, and the FreeBSD platform provider cannot apply them yet (tzsetup/rc.conf keymap/login.conf).",
                failed_operation="Validate FreeBSD platform provider",
                current_state="No disk has been modified.",
                possible_recovery="Leave locale, keymap and timezone unset to keep the live system's settings.",
            )

    def configure_hostname(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        hostname = context.metadata["hostname"]
        rc_conf = target_root / "etc" / "rc.conf"
        rc_conf.parent.mkdir(parents=True, exist_ok=True)

        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        lines = [line for line in lines if not line.startswith("hostname=")]
        lines.append(f'hostname="{hostname}"')
        rc_conf.write_text("\n".join(lines) + "\n")

        hosts_file = target_root / "etc" / "hosts"
        hosts_content = (
            "::1                     localhost localhost.my.domain\n"
            "127.0.0.1               localhost localhost.my.domain\n"
            f"127.0.1.1               {hostname}\n"
        )
        hosts_file.write_text(hosts_content)

    def verify_hostname(self, context: ExecutionContext) -> None:
        expected = context.metadata["hostname"]
        rc_conf = Path(context.target_mount) / "etc" / "rc.conf"
        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        values = [line.split("=", 1)[1].strip('"') for line in lines if line.startswith("hostname=")]
        if values != [expected]:
            raise VerificationError(
                message=f"FreeBSD target hostname entries are {values}, expected [{expected!r}].",
                cause="/etc/rc.conf on the target does not set exactly the chosen hostname.",
                failed_operation="Verify FreeBSD target hostname",
                current_state=f"hostname= entries: {values}",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD target hostname verified: {expected}")

    def configure_locale(self, context: ExecutionContext) -> None:
        # validate() refuses any requested change, so only "keep the live settings" reaches here
        self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the live system")

    def verify_locale(self, context: ExecutionContext) -> None:
        if any(context.metadata.get(k) for k in ("locale", "keymap", "timezone")):
            raise VerificationError(
                message="FreeBSD locale settings were requested but cannot be applied.",
                cause="The FreeBSD platform provider has no locale step yet.",
                failed_operation="Verify FreeBSD locale settings",
            )


    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)
