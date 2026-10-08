"""Online components with apk (Chimera Linux; hybrid-d77). AGENTS.md "Online rules", level A.

- Mirror: the user may choose one from the distribution's mirror list
  ([online].mirror_list, fetched only when there is a network). It is written
  to the target as /etc/apk/repositories.d/00-chimera-mirror.list
  ("set CHIMERA_REPO_URL=<mirror>"), exactly as Chimera's installer does, and
  overrides the repositories' "set -default" value. No choice ("Default")
  writes nothing.
- Packages: installed with the target's own apk in a chroot of the target.

validate() runs before confirmation: the chosen mirror must answer, and the
packages must resolve (apk --simulate on a throwaway root, never the live's
database).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import re
import shutil
import tempfile
import urllib.request

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target

MIRROR_FILE = "etc/apk/repositories.d/00-chimera-mirror.list"
KEYS_DIR = "/usr/lib/apk/keys"


def parse_mirror_list(text: str) -> List[Dict[str, str]]:
    """'URL description...' lines (Chimera's mirrors.txt) -> [{url, description}]."""
    result = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].startswith("https://"):
            result.append({"url": parts[0].rstrip("/"), "description": parts[1] if len(parts) > 1 else ""})
    return result


def fetch_mirrors(list_url: str, opener=urllib.request.urlopen) -> List[Dict[str, str]]:
    with opener(list_url, timeout=20) as response:
        return parse_mirror_list(response.read().decode(errors="replace"))


def mirror_line(mirror: str) -> str:
    return f"set CHIMERA_REPO_URL={mirror}\n"


def apk_simulate(runner, workdir: Path, repositories: str, packages: List[str]):
    """Resolves `apk add <packages>` against `repositories` in a throwaway root under workdir.

    The throwaway root gets a real (empty) database and real indexes -- apk
    --simulate cannot create them -- and the transaction itself is only
    simulated. The live's own database is never touched.
    """
    root = workdir / "root"
    root.mkdir(exist_ok=True)
    repos = workdir / "repositories"
    repos.write_text(repositories)
    common = ["apk", "--root", str(root), "--no-interactive", "--keys-dir", KEYS_DIR, "--repositories-file", str(repos)]
    for step in (["--initdb", "add"], ["update"]):
        proc = runner.run(common + step, phase=EventPhase.PLAN, check=False)
        if proc.returncode != 0:
            return proc
    return runner.run(common + ["--simulate", "add"] + list(packages), phase=EventPhase.PLAN, check=False)


class ApkOnlineProvider(ProviderContract):
    def __init__(self, name: str = "apk", event_stream: Optional[EventStream] = None, opener=urllib.request.urlopen) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)
        self.opener = opener

    def capabilities(self) -> List[str]:
        return ["online", "mirrors"]

    @staticmethod
    def _online(context: ExecutionContext) -> Optional[Dict[str, Any]]:
        online = context.metadata.get("online")
        return online if online and online.get("enabled") else None

    def mirrors(self, list_url: str) -> List[Dict[str, str]]:
        """The distribution's mirror list (needs the network); used by the frontends."""
        return fetch_mirrors(list_url, self.opener)

    def validate(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        report = []
        mirror = online.get("mirror")
        if mirror:
            if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?", mirror):
                raise ExecutionError(message=f"Invalid mirror URL {mirror!r}.", cause="Mirrors are https:// URLs.",
                                     failed_operation="Validate mirror", current_state="No disk has been modified.")
            probe = f"{mirror.rstrip('/')}/current/main/x86_64/APKINDEX.tar.gz"
            try:
                with self.opener(urllib.request.Request(probe, method="HEAD"), timeout=20) as response:
                    status = getattr(response, "status", 200)
            except OSError as err:
                raise ExecutionError(message=f"The mirror {mirror} does not answer.", cause=str(err),
                                     failed_operation="Check the chosen mirror", current_state="No disk has been modified.",
                                     possible_recovery="Connect to a network or choose another mirror (or Default).")
            if status >= 400:
                raise ExecutionError(message=f"The mirror {mirror} answered HTTP {status}.", cause=probe,
                                     failed_operation="Check the chosen mirror", current_state="No disk has been modified.")
            report.append(f"mirror {mirror} answers ({probe})")
        if online["packages"]:
            if not shutil.which("apk"):
                raise ExecutionError(message="apk not found in the live system.", cause="Packages are resolved with apk.",
                                     failed_operation="Resolve online packages")
            with tempfile.TemporaryDirectory(prefix="mocinha-apk-") as tmp:
                lines = [mirror_line(mirror)] if mirror else []
                for d in ("/usr/lib/apk/repositories.d", "/etc/apk/repositories.d"):
                    for f in sorted(Path(d).glob("*.list")) if Path(d).is_dir() else []:
                        lines.append(f.read_text())
                proc = apk_simulate(self.runner, Path(tmp), "".join(lines), list(online["packages"]))
                if proc.returncode != 0:
                    raise ExecutionError(message="Some online packages cannot be resolved.",
                                         cause=(proc.stderr or proc.stdout).strip()[-800:],
                                         failed_operation="Resolve online packages", current_state="No disk has been modified.")
            report.append(f"packages resolve: {online['packages']}")
        context.metadata["online_report"] = [l for l in context.metadata.get("online_report", [])
                                             if l.startswith("bootstrap:")] + report
        for line in report:
            self.events.info(EventPhase.PLAN, f"Online preflight: {line}")

    def apply(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        root = Path(context.target_mount)
        if online.get("mirror"):
            f = root / MIRROR_FILE
            f.parent.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.CONFIGURE, f"Setting the apk mirror of the target to {online['mirror']} (/{MIRROR_FILE})")
            f.write_text(mirror_line(online["mirror"]))
        if online["packages"]:
            run_in_target(self.runner, str(root), ["apk", "--no-interactive", "update"], phase=EventPhase.DEPLOY, network=True)
            run_in_target(self.runner, str(root), ["apk", "--no-interactive", "add"] + list(online["packages"]),
                          phase=EventPhase.DEPLOY, network=True)

    def verify(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        root = Path(context.target_mount)
        problems = []
        if online.get("mirror"):
            f = root / MIRROR_FILE
            if not f.is_file() or f.read_text() != mirror_line(online["mirror"]):
                problems.append(f"/{MIRROR_FILE} does not select {online['mirror']}")
        if online["packages"]:
            proc = run_in_target(self.runner, str(root), ["apk", "info", "-e"] + list(online["packages"]),
                                 phase=EventPhase.VERIFY, check=False)
            installed = set(proc.stdout.split())
            problems += [f"package {p} not installed" for p in online["packages"] if p not in installed]
        if problems:
            raise VerificationError(message="Online components do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify online components")
        self.events.info(EventPhase.VERIFY, f"Online components verified (mirror {online.get('mirror') or 'Default'}).")
