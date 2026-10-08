"""Bootstrap deployment with chimera-bootstrap (Chimera Linux). AGENTS.md "Online rules", level B.

Runs the live's own `chimera-bootstrap [-m MIRROR] <target> <packages>` in
network mode: apk installs chimerautils into an empty root, then the package
set the resolver built from the bootstrap profile and the user's choices.
The mirror is the one the user chose ([online].mirror_list); without one,
the distribution's default repositories are used.

validate() (before confirmation, needs the network) resolves the whole
transaction with `apk --simulate` on a throwaway root, using the same
repositories chimera-bootstrap would use (the live's /etc/apk/repositories
and repositories.d, plus the chosen mirror) -- never the live's database.
"""

from pathlib import Path
from typing import List, Optional
import re
import shutil
import tempfile

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target
from mocinha.providers.online.apk import apk_simulate, mirror_line

INSTALLING = re.compile(r"\(\s*\d+/\d+\)\s+Installing\s+(\S+)\s")


def repositories_file(mirror: Optional[str], live_root: Path = Path("/")) -> str:
    """The repositories chimera-bootstrap uses (its make_reposf), as one file."""
    parts = [mirror_line(mirror)] if mirror else []
    conf = live_root / "etc/apk/repositories"
    if conf.is_file():
        parts.append(conf.read_text())
    files = {}
    for d in ("usr/lib/apk/repositories.d", "etc/apk/repositories.d"):  # /etc overrides same-named files
        for f in sorted((live_root / d).glob("*")) if (live_root / d).is_dir() else []:
            if f.is_file():
                files[f.name] = f.read_text()
    parts += [files[name] for name in sorted(files)]
    return "\n".join(parts)


class ChimeraBootstrapDeploymentProvider(ProviderContract):
    def __init__(self, name: str = "chimera-bootstrap", event_stream: Optional[EventStream] = None,
                 live_root: Path = Path("/")) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)
        self.live_root = live_root

    def capabilities(self) -> List[str]:
        return ["deployment", "bootstrap"]

    @staticmethod
    def _bootstrap(context: ExecutionContext) -> dict:
        bootstrap = context.metadata.get("bootstrap")
        if not bootstrap:
            raise ExecutionError(message="The chimera-bootstrap deployment needs a [bootstrap] profile.",
                                 cause="The package set comes from the profile and the user's choices.",
                                 failed_operation="Validate bootstrap deployment")
        return bootstrap

    @staticmethod
    def _mirror(context: ExecutionContext) -> Optional[str]:
        return (context.metadata.get("online") or {}).get("mirror")

    def validate(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        for tool in ("chimera-bootstrap", "apk"):
            if not shutil.which(tool):
                raise ExecutionError(message=f"{tool} not found in the live system.",
                                     cause="Bootstrap installs use the live's chimera-install-scripts and apk.",
                                     failed_operation="Validate bootstrap tools")
        mirror = self._mirror(context)
        with tempfile.TemporaryDirectory(prefix="mocinha-apk-") as tmp:
            proc = apk_simulate(self.runner, Path(tmp), repositories_file(mirror, self.live_root),
                                ["chimerautils"] + bootstrap["packages"])
        if proc.returncode != 0:
            raise ExecutionError(
                message="The bootstrap package set cannot be resolved from the repositories.",
                cause=(proc.stderr or proc.stdout).strip()[-800:],
                failed_operation="Resolve the bootstrap package set",
                current_state="No disk has been modified.",
                possible_recovery="Connect to a network (mocinha network), check the mirror and the package names.",
            )
        resolved = INSTALLING.findall(proc.stdout)
        report = context.metadata.setdefault("online_report", [])
        report[:] = [l for l in report if not l.startswith("bootstrap:")]
        report.append(f"bootstrap: repositories via {mirror or 'the distribution default'}")
        report.append(f"bootstrap: {len(resolved)} packages to download and install (dependencies included), "
                      f"kernel {bootstrap['kernel']}")
        context.metadata["bootstrap_resolved"] = resolved
        for line in report:
            self.events.info(EventPhase.PLAN, f"Bootstrap preflight: {line}")

    def apply(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        mirror = self._mirror(context)
        cmd = ["chimera-bootstrap"] + (["-m", mirror] if mirror else []) + [context.target_mount] + bootstrap["packages"]
        self.events.action(EventPhase.DEPLOY, f"Bootstrapping {len(bootstrap['packages'])} packages with chimera-bootstrap")
        self.runner.run(cmd, phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        bootstrap = self._bootstrap(context)
        proc = run_in_target(self.runner, context.target_mount, ["apk", "info"], phase=EventPhase.VERIFY)
        installed = set(proc.stdout.split())
        expected = context.metadata.get("bootstrap_resolved") or bootstrap["packages"]
        missing = [p for p in expected if p not in installed]
        if missing:
            raise VerificationError(message="The bootstrapped system is incomplete.",
                                    cause=f"packages not installed: {missing[:20]}",
                                    failed_operation="Verify bootstrap",
                                    possible_recovery="Inspect the chimera-bootstrap output in the event log.")
        self.events.info(EventPhase.VERIFY, f"Bootstrap verified: {len(installed)} packages installed on the target.")
