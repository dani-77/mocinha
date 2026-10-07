"""FreeBSD storage provider using gpart (au-d77).

Creates a hybrid GPT layout that boots on both BIOS and UEFI firmware:

    p1  freebsd-boot  512K   (pmbr + gptboot for BIOS)
    p2  efi           200M   (loader.efi for UEFI)
    p3  freebsd-swap  [optional, manifest swap_size]
    pN  freebsd-ufs   rest

This mirrors the GPT path of au-d77's own installer. MBR layouts are not
implemented and are refused explicitly.
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDStorageProvider(ProviderContract):
    """FreeBSD partitioner using gpart."""

    def __init__(self, name: str = "freebsd-gpart", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["storage", "partitioning"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("gpart"):
            raise ExecutionError(
                message="gpart utility not found.",
                cause="gpart is required for FreeBSD storage partitioning.",
                failed_operation="Validate gpart binary",
                possible_recovery="Ensure gpart is available in FreeBSD userland.",
            )
        if not Path(context.target_disk).exists():
            raise ExecutionError(
                message=f"Target disk does not exist: {context.target_disk}",
                cause="Disk path is invalid or disconnected.",
                failed_operation="Validate FreeBSD target disk path",
                current_state=context.target_disk,
                possible_recovery="Verify disk selection.",
            )
        table = context.metadata.get("partition_table") or "gpt"
        if table != "gpt":
            raise ExecutionError(
                message=f"The gpart provider does not implement {table.upper()} layouts.",
                cause="Only the hybrid GPT layout (BIOS + UEFI) is implemented.",
                failed_operation="Validate partition layout",
                current_state=f"partition_table={table}",
                possible_recovery="Use partition_table = \"gpt\" in the manifest.",
            )
        for bootcode in ("/boot/pmbr", "/boot/gptboot"):
            if not Path(bootcode).is_file():
                raise ExecutionError(
                    message=f"BIOS boot code {bootcode} not found in the live system.",
                    cause="The hybrid GPT layout installs pmbr and gptboot from the live /boot.",
                    failed_operation="Validate BIOS boot code",
                    possible_recovery="Check the live image's /boot directory.",
                )

    def prepare(self, context: ExecutionContext) -> None:
        # Unmount anything still mounted from the target disk (exact disk match, not prefix)
        disk_name = Path(context.target_disk).name
        proc = self.runner.run(["mount", "-p"], phase=EventPhase.PREPARE, check=True)
        for line in reversed(proc.stdout.splitlines()):
            parts = line.split()
            if len(parts) >= 2 and _belongs_to_disk(parts[0], disk_name):
                self.events.action(EventPhase.PREPARE, f"Unmounting stale target mount: {parts[1]}")
                self.runner.run(["umount", "-f", parts[1]], phase=EventPhase.PREPARE, check=True)

    def apply(self, context: ExecutionContext) -> None:
        disk_name = Path(context.target_disk).name
        swap_size = context.metadata.get("swap_size")
        self.events.action(EventPhase.PREPARE, f"Creating hybrid GPT layout on {context.target_disk}")

        # An empty disk has no table to destroy; destroy failing there is expected.
        self.runner.run(["gpart", "destroy", "-F", disk_name], phase=EventPhase.PREPARE, check=False)
        self.runner.run(["gpart", "create", "-s", "gpt", disk_name], phase=EventPhase.PREPARE, check=True)

        def add(*args: str) -> None:
            self.runner.run(["gpart", "add", *args, disk_name], phase=EventPhase.PREPARE, check=True)

        # Labels are prefixed with the system id: generic names such as "efiboot"
        # may already exist on the live medium, and duplicate GPT labels make
        # /dev/gpt/<label> ambiguous while both disks are attached.
        prefix = context.metadata["system_id"]
        labels = {role: f"{prefix}-{role}" for role in ("boot", "efi", "swap", "root")}
        context.metadata["partition_labels"] = {role: f"gpt/{label}" for role, label in labels.items()}

        add("-t", "freebsd-boot", "-s", "512k", "-l", labels["boot"])
        self.runner.run(
            ["gpart", "bootcode", "-b", "/boot/pmbr", "-p", "/boot/gptboot", "-i", "1", disk_name],
            phase=EventPhase.PREPARE,
            check=True,
        )
        add("-t", "efi", "-s", context.metadata["esp_size"], "-l", labels["efi"])
        index = 3
        partitions: Dict[str, str] = {"esp": f"/dev/{disk_name}p2"}
        if swap_size:
            add("-t", "freebsd-swap", "-s", swap_size, "-l", labels["swap"])
            partitions["swap"] = f"/dev/{disk_name}p{index}"
            index += 1
        add("-t", "freebsd-ufs", "-l", labels["root"])
        partitions["root"] = f"/dev/{disk_name}p{index}"
        context.target_partitions.update(partitions)

    def verify(self, context: ExecutionContext) -> None:
        disk_name = Path(context.target_disk).name
        proc = self.runner.run(["gpart", "show", "-p", disk_name], phase=EventPhase.VERIFY, check=True)
        found = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            # "<start> <size> <provider> <type> (<human size>)"
            if len(parts) >= 4 and parts[2].startswith(disk_name + "p"):
                found[f"/dev/{parts[2]}"] = parts[3]
        expected = {f"/dev/{disk_name}p1": "freebsd-boot"}
        expected[context.target_partitions["esp"]] = "efi"
        expected[context.target_partitions["root"]] = "freebsd-ufs"
        if "swap" in context.target_partitions:
            expected[context.target_partitions["swap"]] = "freebsd-swap"
        wrong = {dev: (want, found.get(dev)) for dev, want in expected.items() if found.get(dev) != want}
        missing_nodes = [dev for dev in expected if not Path(dev).exists()]
        if wrong or missing_nodes or "GPT" not in proc.stdout:
            raise VerificationError(
                message=f"Partition layout on {context.target_disk} does not match the plan.",
                cause=f"Type mismatches (expected, found): {wrong}; missing device nodes: {missing_nodes}",
                failed_operation="Verify FreeBSD GPT layout",
                current_state=proc.stdout.strip(),
                possible_recovery="Inspect the gpart output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD GPT layout verified: {expected}")


def _belongs_to_disk(node: str, disk_name: str) -> bool:
    """True for /dev/<disk>, /dev/<disk>pN and /dev/<disk>sN[x]; not for /dev/<disk>0..."""
    import re

    return re.fullmatch(rf"/dev/{re.escape(disk_name)}((p[0-9]+)|(s[0-9]+[a-z]?))?", node) is not None
