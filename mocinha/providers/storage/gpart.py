"""FreeBSD storage provider using gpart (au-d77).

Creates a hybrid GPT layout that boots on both BIOS and UEFI firmware:

    p1  freebsd-boot  512K   (pmbr + gptboot for BIOS)
    p2  efi           200M   (loader.efi for UEFI)
    p3  freebsd-swap  [optional, manifest swap_size]
    pN  freebsd-ufs   rest

This mirrors the GPT path of au-d77's own installer. Its BIOS-only MBR
layout (partition_table = "mbr") is implemented the same way:

    s1  freebsd slice, 1 MiB aligned, active; /boot/mbr in the MBR
      a  freebsd-ufs   slice - swap - 32M  (root; /boot/boot in the slice)
      b  freebsd-swap  the rest            (glabel <system id>-swap, for fstab)
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, release_planned_mounts


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
        if table not in ("gpt", "mbr"):
            raise ExecutionError(
                message=f"The gpart provider does not implement {table.upper()} layouts.",
                cause="The hybrid GPT layout (BIOS + UEFI) and the BIOS MBR layout are implemented.",
                failed_operation="Validate partition layout",
                current_state=f"partition_table={table}",
                possible_recovery="Use partition_table = \"gpt\" or \"mbr\" in the manifest.",
            )
        if table == "mbr" and context.metadata["firmware"].upper() != "BIOS":
            raise ExecutionError(
                message="The MBR layout only boots in legacy BIOS mode.",
                cause=f"Firmware is {context.metadata['firmware']}; the MBR layout has no EFI system partition.",
                failed_operation="Validate partition layout",
                current_state="No disk has been modified.",
                possible_recovery="Use the GPT layout on UEFI.",
            )
        bootcodes = ("/boot/pmbr", "/boot/gptboot") if table == "gpt" else ("/boot/mbr", "/boot/boot")
        for bootcode in bootcodes:
            if not Path(bootcode).is_file():
                raise ExecutionError(
                    message=f"BIOS boot code {bootcode} not found in the live system.",
                    cause=f"The {table.upper()} layout installs {' and '.join(bootcodes)} from the live /boot.",
                    failed_operation="Validate BIOS boot code",
                    possible_recovery="Check the live image's /boot directory.",
                )

    def prepare(self, context: ExecutionContext) -> None:
        release_planned_mounts(self.runner, context, self.events)

    def apply(self, context: ExecutionContext) -> None:
        if (context.metadata.get("partition_table") or "gpt") == "mbr":
            return self._apply_mbr(context)
        disk_name = Path(context.target_disk).name
        swap_size = context.metadata.get("swap_size")
        self.events.action(EventPhase.PREPARE, f"Creating hybrid GPT layout on {context.target_disk}")

        self._destroy_tables(disk_name)
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

    def _destroy_tables(self, disk_name: str) -> None:
        """Destroys the disk's partition table, nested ones first (e.g. a BSD disklabel inside an
        MBR slice), as au-d77-install does; an empty disk has none, so failures are expected."""
        out = self.runner.run(["gpart", "show", disk_name], phase=EventPhase.PREPARE, check=False).stdout or ""
        children = [l.split()[3] for l in out.splitlines() if l.lstrip().startswith("=>") and len(l.split()) >= 4]
        for child in [c for c in children if c != disk_name]:
            self.runner.run(["gpart", "destroy", "-F", child], phase=EventPhase.PREPARE, check=False)
        self.runner.run(["gpart", "destroy", "-F", disk_name], phase=EventPhase.PREPARE, check=False)

    @staticmethod
    def _size_mib(size: str) -> int:
        return int(size[:-1]) * (1024 if size[-1] in "gG" else 1)

    def _apply_mbr(self, context: ExecutionContext) -> None:
        disk_name = Path(context.target_disk).name
        slice_name = f"{disk_name}s1"
        swap_size = context.metadata.get("swap_size")
        self.events.action(EventPhase.PREPARE, f"Creating BIOS MBR layout on {context.target_disk}")

        def gpart(*args: str, check: bool = True):
            return self.runner.run(["gpart", *args], phase=EventPhase.PREPARE, check=check)

        self._destroy_tables(disk_name)
        gpart("create", "-s", "mbr", disk_name)
        gpart("add", "-t", "freebsd", "-a", "1m", disk_name)
        gpart("set", "-a", "active", "-i", "1", disk_name)
        gpart("bootcode", "-b", "/boot/mbr", disk_name)
        # A disklabel left by an earlier install at the same offset is tasted by GEOM as
        # soon as the slice exists ("gpart create -s bsd: File exists" on real hardware);
        # au-d77-install destroys it the same way before creating the BSD scheme.
        gpart("destroy", "-F", slice_name, check=False)
        gpart("create", "-s", "bsd", slice_name)
        partitions: Dict[str, str] = {"root": f"/dev/{slice_name}a"}
        if swap_size:
            out = self.runner.run(["diskinfo", f"/dev/{slice_name}"], phase=EventPhase.PREPARE, check=True).stdout.split()
            root_mib = int(out[2]) // 2**20 - self._size_mib(swap_size) - 32
            if root_mib < 3000:
                raise ExecutionError(
                    message=f"{context.target_disk} is too small for a {swap_size} swap partition.",
                    cause=f"Only {root_mib} MiB would be left for the root filesystem.",
                    failed_operation="Create the BSD partitions",
                    current_state=f"MBR slice {slice_name} created, no BSD partition yet.",
                    possible_recovery="Choose a larger disk.",
                )
            gpart("add", "-t", "freebsd-ufs", "-s", f"{root_mib}m", "-i", "1", slice_name)
            gpart("add", "-t", "freebsd-swap", "-i", "2", slice_name)
            partitions["swap"] = f"/dev/{slice_name}b"
            label = f"{context.metadata['system_id']}-swap"
            self.runner.run(["glabel", "label", label, partitions["swap"]], phase=EventPhase.PREPARE, check=True)
            context.metadata["partition_labels"] = {"swap": f"label/{label}"}
        else:
            gpart("add", "-t", "freebsd-ufs", "-i", "1", slice_name)
            context.metadata["partition_labels"] = {}
        gpart("bootcode", "-b", "/boot/boot", slice_name)
        context.target_partitions.update(partitions)

    def _verify_mbr(self, context: ExecutionContext) -> None:
        disk_name = Path(context.target_disk).name
        slice_name = f"{disk_name}s1"
        outer = self.runner.run(["gpart", "show", "-p", disk_name], phase=EventPhase.VERIFY, check=True).stdout
        inner = self.runner.run(["gpart", "show", "-p", slice_name], phase=EventPhase.VERIFY, check=True).stdout
        problems = []
        if "MBR" not in outer or not any(l.split()[2:4] == [slice_name, "freebsd"] and "[active]" in l
                                         for l in outer.splitlines() if len(l.split()) >= 4):
            problems.append(f"no active freebsd slice {slice_name}")
        expected = {f"{slice_name}a": "freebsd-ufs"}
        if "swap" in context.target_partitions:
            expected[f"{slice_name}b"] = "freebsd-swap"
        found = {l.split()[2]: l.split()[3] for l in inner.splitlines() if len(l.split()) >= 4}
        if "BSD" not in inner or any(found.get(k) != v for k, v in expected.items()):
            problems.append(f"BSD partitions {found}, expected {expected}")
        missing = [d for d in context.target_partitions.values() if not Path(d).exists()]
        if missing:
            problems.append(f"missing device nodes {missing}")
        swap_label = context.metadata.get("partition_labels", {}).get("swap")
        if swap_label and not Path(f"/dev/{swap_label}").exists():
            problems.append(f"/dev/{swap_label} does not exist")
        if problems:
            raise VerificationError(
                message=f"Partition layout on {context.target_disk} does not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify FreeBSD MBR layout",
                current_state=(outer + inner).strip(),
                possible_recovery="Inspect the gpart output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD MBR layout verified: {expected}")

    def verify(self, context: ExecutionContext) -> None:
        if (context.metadata.get("partition_table") or "gpt") == "mbr":
            return self._verify_mbr(context)
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
