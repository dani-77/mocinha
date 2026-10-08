"""Bootstrap deployment with pacstrap (Arch family). AGENTS.md "Online rules", level B.

The target is composed from the repositories instead of copied from the
live: pacstrap -K (fresh keyring in the target) installs the package set the
resolver built from the bootstrap profile and the user's choices. The live's
pacman configuration and mirrorlist are used as they are (pacstrap copies the
mirrorlist into the target); mirrors are listed in the preflight report and
never re-ranked.

validate() (before confirmation) needs the network: it syncs a throwaway
package database and resolves the whole transaction, so a missing package
or an unreachable mirror stops before any disk is touched.
"""

from pathlib import Path
from typing import List, Optional
import shutil
import tempfile

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target
from mocinha.providers.pacman_common import mirror_servers, resolve_packages, scratch_db, sync_scratch


class PacstrapDeploymentProvider(ProviderContract):
    def __init__(self, name: str = "pacstrap", event_stream: Optional[EventStream] = None,
                 live_pacman_conf: Path = Path("/etc/pacman.conf"),
                 live_mirrorlist: Path = Path("/etc/pacman.d/mirrorlist")) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)
        self.live_pacman_conf = live_pacman_conf
        self.live_mirrorlist = live_mirrorlist

    def capabilities(self) -> List[str]:
        return ["deployment", "bootstrap"]

    @staticmethod
    def _bootstrap(context: ExecutionContext) -> dict:
        bootstrap = context.metadata.get("bootstrap")
        if not bootstrap:
            raise ExecutionError(message="The pacstrap deployment needs a [bootstrap] profile.",
                                 cause="The package set comes from the profile and the user's choices.",
                                 failed_operation="Validate bootstrap deployment")
        return bootstrap

    def validate(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        for tool in ("pacstrap", "pacman"):
            if not shutil.which(tool):
                raise ExecutionError(message=f"{tool} not found in the live system.",
                                     cause="Bootstrap installs use the live's pacstrap (arch-install-scripts).",
                                     failed_operation="Validate bootstrap tools")
        mirrors = mirror_servers(self.live_mirrorlist)
        if not mirrors:
            raise ExecutionError(message=f"No active mirror in {self.live_mirrorlist}.",
                                 cause="pacstrap downloads from the live's mirrors (they are never chosen silently).",
                                 failed_operation="Validate mirrors", current_state="No disk has been modified.",
                                 possible_recovery="Uncomment a Server line in the mirrorlist (or run reflector yourself).")
        with tempfile.TemporaryDirectory(prefix="mocinha-pacstrap-") as tmp:
            opts = scratch_db(Path(tmp), self.live_pacman_conf)
            sync_scratch(self.runner, opts)
            resolved = resolve_packages(self.runner, opts, bootstrap["packages"])
        report = context.metadata.setdefault("online_report", [])
        report[:] = [l for l in report if not l.startswith("bootstrap:")]
        report.append(f"bootstrap: mirrors (in this order, as on the live): {mirrors}")
        report.append(f"bootstrap: {len(resolved)} packages to download and install (dependencies included), "
                      f"kernel {bootstrap['kernel']}")
        context.metadata["bootstrap_resolved"] = [l.split()[0] for l in resolved]
        for line in report:
            self.events.info(EventPhase.PLAN, f"Bootstrap preflight: {line}")

    def apply(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        target = context.target_mount
        self.events.action(EventPhase.DEPLOY, f"Bootstrapping {len(bootstrap['packages'])} packages into {target} with pacstrap")
        self.runner.run(["pacstrap", "-K", target] + bootstrap["packages"], phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        root = Path(context.target_mount)
        proc = run_in_target(self.runner, str(root), ["pacman", "-Qq"], phase=EventPhase.VERIFY)
        installed = set(proc.stdout.split())
        expected = context.metadata.get("bootstrap_resolved") or bootstrap["packages"]
        missing = [p for p in expected if p not in installed]
        problems = [f"packages not installed: {missing[:20]}"] if missing else []
        if not (root / "etc" / "pacman.d" / "mirrorlist").is_file():
            problems.append("/etc/pacman.d/mirrorlist missing on the target")
        if not any((root / "etc" / "pacman.d" / "gnupg" / f).exists() for f in ("pubring.gpg", "pubring.kbx")):
            problems.append("the target has no pacman keyring")
        if problems:
            raise VerificationError(message="The bootstrapped system is incomplete.", cause="; ".join(problems),
                                    failed_operation="Verify bootstrap",
                                    possible_recovery="Inspect the pacstrap output in the event log.")
        self.events.info(EventPhase.VERIFY, f"Bootstrap verified: {len(installed)} packages installed on the target.")
