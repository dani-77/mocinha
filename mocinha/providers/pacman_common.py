"""pacman helpers shared by the online (level A) and bootstrap (level B) providers.

The preflight never touches the live's package database: it uses a throwaway
sync database (temporary --dbpath/--cachedir/--logfile) with the live's
pacman.conf, plus any extra repositories.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from mocinha.core.errors import ExecutionError
from mocinha.core.events import EventPhase


def repository_block(repo: Dict[str, Any]) -> str:
    return f"\n[{repo['name']}]\nSigLevel = {repo['siglevel']}\n" + "".join(f"Server = {s}\n" for s in repo["servers"])


def configured_repositories(pacman_conf: str) -> List[str]:
    return [m.group(1) for m in re.finditer(r"^\s*\[([^\]]+)\]\s*$", pacman_conf, re.M) if m.group(1) != "options"]


def mirror_servers(mirrorlist: Path, limit: int = 3) -> List[str]:
    """The first active Server lines of a mirrorlist, in the order pacman uses them."""
    if not mirrorlist.is_file():
        return []
    servers = [l.split("=", 1)[1].strip() for l in mirrorlist.read_text().splitlines()
               if l.strip().startswith("Server") and "=" in l]
    return servers[:limit]


def scratch_db(workdir: Path, live_conf: Path, extra_repositories: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """pacman options for a throwaway sync database based on the live's configuration."""
    if not live_conf.is_file():
        raise ExecutionError(message=f"{live_conf} not found in the live system.",
                             cause="Package preflight uses the live's pacman configuration.",
                             failed_operation="Prepare package preflight")
    text = live_conf.read_text()
    present = configured_repositories(text)
    text += "".join(repository_block(r) for r in extra_repositories or [] if r["name"] not in present)
    conf = workdir / "pacman.conf"
    conf.write_text(text)
    for d in ("db", "cache"):
        (workdir / d).mkdir(exist_ok=True)
    return ["--config", str(conf), "--dbpath", str(workdir / "db"), "--cachedir", str(workdir / "cache"),
            "--logfile", str(workdir / "pacman.log"), "--noconfirm"]


def sync_scratch(runner, opts: List[str]) -> None:
    proc = runner.run(["pacman"] + opts + ["-Sy"], phase=EventPhase.PLAN, check=False)
    if proc.returncode != 0:
        raise ExecutionError(
            message="Synchronizing the package databases failed (network or repository problem).",
            cause=(proc.stderr or proc.stdout).strip()[-800:],
            failed_operation="Synchronize a temporary package database",
            current_state="No disk has been modified.",
            possible_recovery="Connect to a network (mocinha network) or choose an install without network.",
        )


def resolve_packages(runner, opts: List[str], names: List[str]) -> List[str]:
    """'name version repo' for every package the transaction would install (dependencies included)."""
    if not names:
        return []
    proc = runner.run(["pacman"] + opts + ["-Sp", "--print-format", "%n %v %r"] + names,
                      phase=EventPhase.PLAN, check=False)
    if proc.returncode != 0:
        raise ExecutionError(
            message="Some packages cannot be resolved from the repositories.",
            cause=(proc.stderr or proc.stdout).strip()[-800:],
            failed_operation="Resolve packages",
            current_state="No disk has been modified.",
            possible_recovery="Fix the package names (mocinha packages search <term>).",
        )
    return [l for l in proc.stdout.splitlines() if l.strip() and not l.startswith(":")]


def search_packages(runner, opts: List[str], term: str) -> List[Dict[str, str]]:
    """[{repo, name, version, description}] matching term, from the throwaway database."""
    proc = runner.run(["pacman"] + opts + ["-Ss", term], phase=EventPhase.PLAN, check=False)
    results, current = [], None
    for line in proc.stdout.splitlines():
        if not line.startswith(" ") and "/" in line:
            repo_name, _, rest = line.partition(" ")
            repo, _, name = repo_name.partition("/")
            current = {"repo": repo, "name": name, "version": rest.split()[0] if rest else "", "description": ""}
            results.append(current)
        elif current is not None:
            current["description"] = line.strip()
    return results
