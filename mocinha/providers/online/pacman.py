"""Online components with pacman (Arch family; btw-d77). AGENTS.md "Online rules", level A.

Runs after deployment, inside the target:

1. the manifest's extra repositories are appended to the target's
   /etc/pacman.conf (like btw-d77's own installer does), with the signature
   policy the manifest states;
2. the target's keyring is initialized when it has none (a live's keyring is
   often a tmpfs that is not copied);
3. pacman -S (or -Syu when [online].upgrade) installs the packages;
4. AUR packages are built by a temporary unprivileged user at the git
   revision recorded before confirmation, installed with pacman -U, and the
   user and its build tree are removed.

validate() runs before confirmation and never touches the disk or the live's
package state: it syncs a throwaway package database (temporary --dbpath,
the live's pacman.conf plus the extra repositories) to prove every package
and every AUR build dependency resolves, looks the AUR packages up, and
records the revision of each one. The revisions are checked again right
before execution; a changed PKGBUILD stops the install.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import re
import shutil
import tempfile
import urllib.parse
import urllib.request

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.manifest import PACKAGE_NAME
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target

AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
AUR_GIT = "https://aur.archlinux.org/{}.git"
BUILD_USER = "mocinha-build"
BUILD_HOME = "/var/tmp/mocinha-build"
# What makepkg itself needs on the target (the AUR's documented prerequisite), not remaster policy
BUILD_TOOLS = ["base-devel", "git"]


def repository_block(repo: Dict[str, Any]) -> str:
    return f"\n[{repo['name']}]\nSigLevel = {repo['siglevel']}\n" + "".join(f"Server = {s}\n" for s in repo["servers"])


def configured_repositories(pacman_conf: str) -> List[str]:
    return [m.group(1) for m in re.finditer(r"^\s*\[([^\]]+)\]\s*$", pacman_conf, re.M) if m.group(1) != "options"]


def aur_info(names: List[str], opener=urllib.request.urlopen) -> Dict[str, Dict[str, Any]]:
    if not names:
        return {}
    query = urllib.parse.urlencode([("arg[]", n) for n in names])
    with opener(f"{AUR_RPC}?{query}", timeout=30) as response:
        data = json.loads(response.read().decode())
    if data.get("type") == "error":
        raise ExecutionError(message="The AUR RPC returned an error.", cause=str(data.get("error")),
                             failed_operation="Look up AUR packages")
    return {r["Name"]: r for r in data.get("results", [])}


def strip_version(dep: str) -> str:
    return re.split(r"[<>=]", dep, maxsplit=1)[0]


class PacmanOnlineProvider(ProviderContract):
    def __init__(self, name: str = "pacman", event_stream: Optional[EventStream] = None,
                 live_pacman_conf: Path = Path("/etc/pacman.conf")) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)
        self.live_pacman_conf = live_pacman_conf

    def capabilities(self) -> List[str]:
        return ["online", "packages-online"]

    @staticmethod
    def _online(context: ExecutionContext) -> Optional[Dict[str, Any]]:
        online = context.metadata.get("online")
        return online if online and online.get("enabled") else None

    # ------------------------------------------------------------ preflight
    def _scratch_db(self, workdir: Path, online: Dict[str, Any]) -> List[str]:
        """pacman options for a throwaway sync database; the live's own database is not touched."""
        live_conf = self.live_pacman_conf
        if not live_conf.is_file():
            raise ExecutionError(message="/etc/pacman.conf not found in the live system.",
                                 cause="The target's repositories are the live's (the target is a copy of it).",
                                 failed_operation="Prepare package preflight")
        text = live_conf.read_text()
        present = configured_repositories(text)
        text += "".join(repository_block(r) for r in online["repositories"] if r["name"] not in present)
        conf = workdir / "pacman.conf"
        conf.write_text(text)
        (workdir / "db").mkdir()
        (workdir / "cache").mkdir()
        return ["--config", str(conf), "--dbpath", str(workdir / "db"), "--cachedir", str(workdir / "cache"),
                "--logfile", str(workdir / "pacman.log"), "--noconfirm"]

    def validate(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        if not shutil.which("pacman"):
            raise ExecutionError(message="pacman not found in the live system.",
                                 cause="The online preflight resolves packages with the live's pacman.",
                                 failed_operation="Validate online components")
        report: List[str] = []
        with tempfile.TemporaryDirectory(prefix="mocinha-pacman-") as tmp:
            opts = self._scratch_db(Path(tmp), online)
            proc = self.runner.run(["pacman"] + opts + ["-Sy"], phase=EventPhase.PLAN, check=False)
            if proc.returncode != 0:
                raise ExecutionError(
                    message="Synchronizing the package databases failed (network or repository problem).",
                    cause=(proc.stderr or proc.stdout).strip()[-800:],
                    failed_operation="Synchronize a temporary package database",
                    current_state="No disk has been modified.",
                    possible_recovery="Connect to a network (mocinha network) or install without the online components.",
                )
            report.append("repositories reachable: " + ", ".join(
                configured_repositories(Path(tmp, "pacman.conf").read_text())))

            info = aur_info(online["aur"]) if online["aur"] else {}
            missing_aur = [n for n in online["aur"] if n not in info]
            if missing_aur:
                raise ExecutionError(message=f"AUR packages not found: {missing_aur}",
                                     cause="The AUR RPC has no package with these names.",
                                     failed_operation="Look up AUR packages", current_state="No disk has been modified.")
            aur_deps = sorted({strip_version(d) for n in online["aur"]
                               for d in info[n].get("Depends", []) + info[n].get("MakeDepends", [])})
            aur_deps_from_aur = [d for d in aur_deps if d in online["aur"]]
            repo_deps = [d for d in aur_deps if d not in online["aur"]]
            wanted = list(online["packages"]) + repo_deps + (BUILD_TOOLS if online["aur"] else [])
            if wanted:
                proc = self.runner.run(["pacman"] + opts + ["-Sp", "--print-format", "%n %v %r"] + wanted,
                                       phase=EventPhase.PLAN, check=False)
                if proc.returncode != 0:
                    raise ExecutionError(
                        message="Some online packages or AUR build dependencies cannot be resolved.",
                        cause=(proc.stderr or proc.stdout).strip()[-800:],
                        failed_operation="Resolve online packages",
                        current_state="No disk has been modified.",
                        possible_recovery="Fix the package names; AUR dependencies that are themselves AUR "
                                          "packages must be listed as AUR packages too.",
                    )
                report.append(f"{len(proc.stdout.split(chr(10))) - 1} packages resolve from the repositories")

        revisions: Dict[str, str] = {}
        for name in online["aur"]:
            base = info[name]["PackageBase"]
            if not PACKAGE_NAME.match(base):
                raise ExecutionError(message=f"Unexpected AUR package base {base!r} for {name}.",
                                     cause="The name is used in paths and commands.", failed_operation="Pin AUR revisions")
            proc = self.runner.run(["git", "ls-remote", AUR_GIT.format(base), "HEAD"], phase=EventPhase.PLAN, check=False)
            rev = proc.stdout.split()[0] if proc.returncode == 0 and proc.stdout.split() else None
            if not rev:
                raise ExecutionError(message=f"Cannot read the AUR git revision of {name}.", cause=proc.stderr.strip(),
                                     failed_operation="Pin AUR revisions", current_state="No disk has been modified.")
            revisions[name] = rev
            report.append(f"AUR {name} {info[name]['Version']} (base {base}, maintainer "
                          f"{info[name].get('Maintainer')}, unsigned PKGBUILD) at revision {rev[:12]}")
        previous = context.metadata.get("aur_revisions")
        if previous is not None and previous != revisions:
            changed = {n: (previous.get(n), revisions.get(n)) for n in set(previous) | set(revisions)
                       if previous.get(n) != revisions.get(n)}
            raise ExecutionError(
                message="An AUR package changed after the plan was shown.",
                cause=f"(planned, current) revisions: {changed}",
                failed_operation="Re-check AUR revisions before execution",
                current_state="No disk has been modified.",
                possible_recovery="Review the new PKGBUILD and plan again.",
            )
        context.metadata["aur_revisions"] = revisions
        context.metadata["aur_order"] = self._build_order(online["aur"], info)
        context.metadata["aur_info"] = info  # the build uses what the plan showed, not a later lookup
        context.metadata["online_report"] = report
        for line in report:
            self.events.info(EventPhase.PLAN, f"Online preflight: {line}")

    @staticmethod
    def _build_order(names: List[str], info: Dict[str, Dict[str, Any]]) -> List[str]:
        order: List[str] = []
        visiting = set()

        def visit(n: str) -> None:
            if n in order:
                return
            if n in visiting:
                raise ExecutionError(message=f"AUR dependency cycle involving {n}.", cause="Cannot order the builds.",
                                     failed_operation="Order AUR builds")
            visiting.add(n)
            for d in info[n].get("Depends", []) + info[n].get("MakeDepends", []):
                if strip_version(d) in names:
                    visit(strip_version(d))
            order.append(n)

        for n in names:
            visit(n)
        return order

    # ------------------------------------------------------------ apply
    def apply(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        root = context.target_mount
        conf = Path(root) / "etc" / "pacman.conf"
        if not conf.is_file():
            raise ExecutionError(message="/etc/pacman.conf not found on the target.", cause="The target is not an Arch system.",
                                 failed_operation="Add online repositories")
        text = conf.read_text()
        present = configured_repositories(text)
        for repo in online["repositories"]:
            if repo["name"] in present:
                self.events.info(EventPhase.CONFIGURE, f"Repository [{repo['name']}] already configured on the target")
                continue
            self.events.action(EventPhase.CONFIGURE, f"Adding repository [{repo['name']}] (SigLevel = {repo['siglevel']}) to the target")
            text += repository_block(repo)
        conf.write_text(text)

        gnupg = Path(root) / "etc" / "pacman.d" / "gnupg"
        if not any((gnupg / f).exists() for f in ("pubring.gpg", "pubring.kbx")):
            self.events.action(EventPhase.CONFIGURE, "Initializing the target's pacman keyring")
            run_in_target(self.runner, root, ["pacman-key", "--init"])
            run_in_target(self.runner, root, ["pacman-key", "--populate"])

        sync = ["pacman", "-Syu" if online["upgrade"] else "-Sy", "--noconfirm", "--needed"]
        for pattern in online.get("overwrite", []):
            sync += ["--overwrite", pattern]
        packages = list(online["packages"])
        if packages or online["upgrade"]:
            self.events.action(EventPhase.DEPLOY, f"Installing online packages {packages} on the target")
            run_in_target(self.runner, root, sync + packages, phase=EventPhase.DEPLOY, network=True)
        if online["aur"]:
            self._build_aur(context, online)

    def _build_aur(self, context: ExecutionContext, online: Dict[str, Any]) -> None:
        root = context.target_mount
        revisions, order = context.metadata["aur_revisions"], context.metadata["aur_order"]
        info = context.metadata["aur_info"]
        repo_deps = sorted({strip_version(d) for n in online["aur"]
                            for d in info[n].get("Depends", []) + info[n].get("MakeDepends", [])} - set(online["aur"]))
        run_in_target(self.runner, root, ["pacman", "-S", "--noconfirm", "--needed", "--asdeps"] + BUILD_TOOLS + repo_deps,
                      phase=EventPhase.DEPLOY, network=True)
        self.events.action(EventPhase.DEPLOY, f"Creating temporary build user {BUILD_USER}")
        run_in_target(self.runner, root, ["useradd", "--system", "--create-home", "--home-dir", BUILD_HOME,
                                          "--shell", "/usr/bin/nologin", BUILD_USER])
        try:
            for name in order:
                base, rev = info[name]["PackageBase"], revisions[name]
                src = f"{BUILD_HOME}/{base}"
                as_user = ["runuser", "-u", BUILD_USER, "--"]
                self.events.action(EventPhase.DEPLOY, f"Building AUR {name} at {rev[:12]}")
                run_in_target(self.runner, root, as_user + ["git", "clone", "--quiet", AUR_GIT.format(base), src],
                              phase=EventPhase.DEPLOY, network=True)
                run_in_target(self.runner, root, as_user + ["git", "-C", src, "checkout", "--quiet", rev], phase=EventPhase.DEPLOY)
                run_in_target(self.runner, root, ["sh", "-c", f"cd {src} && exec runuser -u {BUILD_USER} -- makepkg --nocheck --noconfirm"],
                              phase=EventPhase.DEPLOY, network=True)
                listing = run_in_target(self.runner, root, ["sh", "-c", f"cd {src} && exec runuser -u {BUILD_USER} -- makepkg --packagelist"],
                                        phase=EventPhase.DEPLOY)
                files = [f for f in listing.stdout.split() if Path(f).name.startswith(f"{name}-")]
                if not files:
                    raise ExecutionError(message=f"makepkg produced no package for {name}.", cause=listing.stdout.strip(),
                                         failed_operation=f"Build AUR {name}")
                run_in_target(self.runner, root, ["pacman", "-U", "--noconfirm"] + files, phase=EventPhase.DEPLOY)
        finally:
            self.events.action(EventPhase.CLEANUP, f"Removing temporary build user {BUILD_USER} and {BUILD_HOME}")
            run_in_target(self.runner, root, ["userdel", "--remove", BUILD_USER], check=False)
            shutil.rmtree(Path(root) / BUILD_HOME.lstrip("/"), ignore_errors=True)

    # ------------------------------------------------------------ verify
    def verify(self, context: ExecutionContext) -> None:
        online = self._online(context)
        if online is None:
            return
        root = Path(context.target_mount)
        problems = []
        present = configured_repositories((root / "etc" / "pacman.conf").read_text())
        problems += [f"repository [{r['name']}] missing from pacman.conf" for r in online["repositories"] if r["name"] not in present]
        names = list(online["packages"]) + list(online["aur"])
        if names:
            proc = run_in_target(self.runner, str(root), ["pacman", "-Q"] + names, phase=EventPhase.VERIFY, check=False)
            installed = {line.split()[0] for line in proc.stdout.splitlines() if line.strip()}
            problems += [f"package {n} not installed" for n in names if n not in installed]
        passwd = (root / "etc" / "passwd").read_text() if (root / "etc" / "passwd").is_file() else ""
        if any(line.startswith(f"{BUILD_USER}:") for line in passwd.splitlines()):
            problems.append(f"temporary build user {BUILD_USER} still exists")
        if (root / BUILD_HOME.lstrip("/")).exists():
            problems.append(f"{BUILD_HOME} still exists")
        if problems:
            raise VerificationError(message="Online components do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify online components",
                                    possible_recovery="Inspect the pacman/makepkg output in the event log.")
        self.events.info(EventPhase.VERIFY, f"Online components verified: {names}, repositories "
                                            f"{[r['name'] for r in online['repositories']]}")
