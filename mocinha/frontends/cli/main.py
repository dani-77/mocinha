"""CLI frontend for Mocinha Installer.

Allows probing, validating manifests, inspecting service graphs,
and previewing installation plans without graphical dependencies.
"""

from pathlib import Path
import argparse
import sys

from mocinha.core.events import Event, EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import SystemProbe
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry, wire_plan_providers


def print_event(e: Event) -> None:
    print(e.format_log_line())


def selected_services(manifest: Manifest, args: argparse.Namespace) -> set:
    """Manifest default_enabled services plus any requested with --services (as the GUI pre-selects them)."""
    requested = set(args.services.split(",")) if args.services else set()
    return set(manifest.services.default_enabled) | requested


def cmd_probe(args: argparse.Namespace) -> int:
    stream = EventStream()
    if args.verbose:
        stream.subscribe(print_event)
    probe = SystemProbe(stream)
    facts = probe.probe_facts()

    print("\n================== MOCINHA SYSTEM PROBE ==================")
    print(f"Platform:       {facts.platform_name}")
    print(f"Architecture:   {facts.arch}")
    print(f"Firmware:       {facts.firmware.value}")
    print(f"Total Memory:   {round(facts.total_memory_bytes / (1024**3), 2)} GiB")
    print("\nDisks detected:")
    if not facts.disks:
        print("  (None found)")
    for d in facts.disks:
        flags = []
        if d.removable:
            flags.append("removable")
        if d.read_only:
            flags.append("read-only")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  - {d.path:<16} {d.size_gib:>6.2f} GiB  model: {d.model}{flag_str}")
    print("==========================================================\n")
    return 0


def cmd_check_manifest(args: argparse.Namespace) -> int:
    path = Path(args.manifest_path)
    try:
        manifest = Manifest.load_from_file(path)
        print(f"\n✓ Manifest valid: {path}")
        print(f"  System:     {manifest.system.name} ({manifest.system.id}) v{manifest.system.version}")
        print(f"  Platform:   {manifest.system.platform} ({manifest.system.arch})")
        print(f"  Method:     {manifest.install.method}")
        print(f"  Boot:       default='{manifest.boot.default}', available={manifest.boot.available}")
        print(f"  Services:   required={manifest.services.required}")
        print(f"              default={manifest.services.default_enabled}")
        print(f"              optional={manifest.services.optional}")
        print(f"              live_only={manifest.services.live_only}\n")
        return 0
    except Exception as e:
        print(f"\n✗ Error in manifest {path}:\n{e}\n", file=sys.stderr)
        return 1


def prepare_plan(args: argparse.Namespace, stream: EventStream):
    """Resolve, wire and validate (read-only) a plan; nothing touches a disk here.

    Returns (plan, context, executor). Raises on any problem, so it is reported
    together with the plan, before confirmation.
    """
    from mocinha.core.executor import InstallationExecutor
    from mocinha.core.provider import build_execution_context

    manifest = Manifest.load_from_file(Path(args.manifest))
    facts = SystemProbe(stream).probe_facts()
    registry = create_default_registry(stream)
    resolver = InstallationResolver(facts, manifest, registry, stream)
    choices = UserChoices(
        target_disk=args.disk,
        bootloader=args.bootloader or manifest.boot.default,
        username=args.user,
        password=args.password,
        hostname=args.hostname,
        root_password=args.root_password or None,
        kernel_args=args.kernel_args.split() if args.kernel_args else [],
        locale=args.locale,
        keymap=args.keymap,
        timezone=args.timezone,
        selected_services=selected_services(manifest, args),
        online=False if args.offline else None,
        online_packages=list(args.online_package or []),
        aur_packages=list(args.aur or []),
        kernel=args.kernel,
        mirror=args.mirror,
    )
    plan = resolver.resolve(choices)
    wire_plan_providers(plan, registry, manifest)
    context = build_execution_context(plan, args.mount, args.password, args.root_password)
    executor = InstallationExecutor(stream)
    executor.preflight(plan, context)
    return plan, context, executor


def print_online_report(context) -> None:
    report = context.metadata.get("online_report")
    if report:
        print("ONLINE PREFLIGHT (checked now, before confirmation):")
        for line in report:
            print(f"  - {line}")
        print()


def cmd_network(args: argparse.Namespace) -> int:
    from mocinha.providers.network import select_network_provider

    stream = EventStream()
    if args.verbose:
        stream.subscribe(print_event)
    provider = select_network_provider(create_default_registry(stream))
    if provider is None:
        print("No supported network stack is running in this live (NetworkManager, iwd).", file=sys.stderr)
        return 1
    try:
        if args.action == "connect":
            if not args.ssid:
                print("connect needs --ssid", file=sys.stderr)
                return 2
            password = None
            if not args.open:
                import getpass
                password = getpass.getpass(f"Password for {args.ssid}: ")
            provider.connect(args.ssid, password, args.device)
        if args.action == "scan":
            for net in provider.scan():
                mark = "*" if net.connected else " "
                signal = f"{net.signal:>3}%" if net.signal is not None else "   ?"
                print(f" {mark} {signal}  {net.security:<10} {net.ssid}")
            return 0
        st = provider.status()
        print(f"{st.detail}\nConnected: {'yes' if st.connected else 'no'}")
        for c in st.connections:
            print(f"  - {c}")
        if st.wifi_devices:
            print(f"Wi-Fi devices: {', '.join(st.wifi_devices)}")
        return 0 if st.connected or args.action != "status" else 1
    except Exception as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1


def cmd_mirrors(args: argparse.Namespace) -> int:
    """Lists the remaster's package mirrors (needs the network)."""
    stream = EventStream()
    try:
        manifest = Manifest.load_from_file(Path(args.manifest))
        if not (manifest.online and manifest.online.mirror_list):
            print("This remaster declares no mirror list ([online].mirror_list).", file=sys.stderr)
            return 1
        provider = create_default_registry(stream).get("online", manifest.providers.online)
        if provider is None or not hasattr(provider, "mirrors"):
            print(f"The online provider {manifest.providers.online!r} cannot list mirrors.", file=sys.stderr)
            return 1
        mirrors = provider.mirrors(manifest.online.mirror_list)
    except Exception as e:
        print(f"\nCannot read the mirror list (is the network up? mocinha network status):\n{e}\n", file=sys.stderr)
        return 1
    print("Default  (the distribution's own repository; no mirror is written)")
    for m in mirrors:
        print(f"{m['url']}  {m['description']}")
    return 0


def cmd_packages(args: argparse.Namespace) -> int:
    """Searches the repositories the target would use, in a throwaway database (never the live's)."""
    import shutil
    import tempfile
    from mocinha.providers.base import CommandRunner
    from mocinha.providers.pacman_common import scratch_db, search_packages, sync_scratch

    if not shutil.which("pacman"):
        print("Package search needs pacman (Arch family lives).", file=sys.stderr)
        return 1
    stream = EventStream()
    if args.verbose:
        stream.subscribe(print_event)
    runner = CommandRunner(stream)
    try:
        with tempfile.TemporaryDirectory(prefix="mocinha-search-") as tmp:
            opts = scratch_db(Path(tmp), Path("/etc/pacman.conf"))
            sync_scratch(runner, opts)
            results = search_packages(runner, opts, args.term)
    except Exception as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1
    for r in results:
        print(f"{r['repo']}/{r['name']} {r['version']}\n    {r['description']}")
    return 0 if results else 1


def cmd_plan(args: argparse.Namespace) -> int:
    stream = EventStream()
    if args.verbose:
        stream.subscribe(print_event)
    try:
        plan, context, _ = prepare_plan(args, stream)
    except Exception as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1
    print("\n" + plan.to_human_readable() + "\n")
    print_online_report(context)
    print("Plan validated by all providers (read-only checks).")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    stream = EventStream()
    stream.subscribe(print_event)
    try:
        plan, context, executor = prepare_plan(args, stream)
    except Exception as e:
        print(f"Planning failed (no disk was modified):\n{e}", file=sys.stderr)
        return 1
    print("\n" + plan.to_human_readable() + "\n")
    print_online_report(context)

    if not args.confirm:
        print("To proceed with destructive execution, pass --confirm.", file=sys.stderr)
        return 1

    try:
        executor.execute_plan(plan, context, confirmed=True)
        print("\n✓ Installation completed and verified successfully.\n")
        return 0
    except Exception as e:
        print(f"\n✗ Installation failed:\n{e}\n", file=sys.stderr)
        return 1


def add_online_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--offline", action="store_true",
                   help="Decline the manifest's optional online components (listed as skipped in the plan)")
    p.add_argument("--online-package", action="append", metavar="PKG", help="Extra package from the repositories (repeatable)")
    p.add_argument("--aur", action="append", metavar="PKG", help="Extra package built from the AUR (repeatable)")
    p.add_argument("--mirror", help="Package mirror from the remaster's mirror list (mocinha mirrors); default: the distribution's")
    p.add_argument("--kernel", help="Bootstrap profiles: kernel package among [bootstrap].kernels (default: the first)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mocinha",
        description="Mocinha Installer --- Small, modular, platform-aware live installer",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose details logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # probe
    p_probe = subparsers.add_parser("probe", help="Probe system and machine facts")
    p_probe.set_defaults(func=cmd_probe)

    # check-manifest
    p_manifest = subparsers.add_parser("check-manifest", help="Validate a mocinha.toml manifest")
    p_manifest.add_argument("manifest_path", help="Path to manifest file")
    p_manifest.set_defaults(func=cmd_check_manifest)

    # plan
    p_plan = subparsers.add_parser("plan", help="Resolve and preview installation plan")
    p_plan.add_argument("--manifest", required=True, help="Path to manifest file")
    p_plan.add_argument("--disk", required=True, help="Target disk path (e.g. /dev/nvme0n1)")
    p_plan.add_argument("--bootloader", help="Requested bootloader (e.g. limine)")
    p_plan.add_argument("--user", required=True, help="Primary user account name")
    p_plan.add_argument("--password", required=True, help="Password for the primary user (needed to validate the plan)")
    p_plan.add_argument("--mount", default="/mnt", help="Staging mount directory (default /mnt)")
    p_plan.add_argument("--hostname", required=True, help="Target hostname")
    p_plan.add_argument("--root-password", default="", help="Root password (default: root account locked)")
    p_plan.add_argument("--kernel-args", default="", help="Extra kernel command-line arguments (e.g. 'console=ttyS0,115200')")
    p_plan.add_argument("--locale", default=None, help="System locale (LANG); default: keep the live setting")
    p_plan.add_argument("--keymap", default=None, help="Console keymap; default: keep the live setting")
    p_plan.add_argument("--timezone", default=None, help="Timezone (e.g. Europe/Lisbon); default: keep the live setting")
    p_plan.add_argument("--services", help="Comma-separated requested services")
    add_online_arguments(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    # install
    p_inst = subparsers.add_parser("install", help="Execute approved installation plan")
    p_inst.add_argument("--manifest", required=True, help="Path to manifest file")
    p_inst.add_argument("--disk", required=True, help="Target disk path (e.g. /dev/vda)")
    p_inst.add_argument("--bootloader", help="Requested bootloader (e.g. limine)")
    p_inst.add_argument("--user", required=True, help="Primary user account name")
    p_inst.add_argument("--password", required=True, help="Password for the primary user")
    p_inst.add_argument("--hostname", required=True, help="Target hostname")
    p_inst.add_argument("--root-password", default="", help="Root password (default: root account locked)")
    p_inst.add_argument("--kernel-args", default="", help="Extra kernel command-line arguments (e.g. 'console=ttyS0,115200')")
    p_inst.add_argument("--locale", default=None, help="System locale (LANG); default: keep the live setting")
    p_inst.add_argument("--keymap", default=None, help="Console keymap; default: keep the live setting")
    p_inst.add_argument("--timezone", default=None, help="Timezone (e.g. Europe/Lisbon); default: keep the live setting")
    p_inst.add_argument("--services", help="Comma-separated requested services")
    add_online_arguments(p_inst)
    p_inst.add_argument("--mount", default="/mnt", help="Staging mount directory (default /mnt)")
    p_inst.add_argument("--confirm", action="store_true", help="Confirm destructive disk modification")
    p_inst.set_defaults(func=cmd_install)

    # mirrors
    p_mir = subparsers.add_parser("mirrors", help="List the remaster's package mirrors (needs the network)")
    p_mir.add_argument("--manifest", default="/etc/mocinha.toml")
    p_mir.set_defaults(func=cmd_mirrors)

    # packages
    p_pkg = subparsers.add_parser("packages", help="Search the repositories (throwaway database; nothing is installed)")
    p_pkg.add_argument("action", choices=["search"])
    p_pkg.add_argument("term")
    p_pkg.set_defaults(func=cmd_packages)

    # network
    p_net = subparsers.add_parser("network", help="Show or set up the live's network connection")
    p_net.add_argument("action", choices=["status", "scan", "connect"], nargs="?", default="status")
    p_net.add_argument("--ssid", help="Wi-Fi network to connect to")
    p_net.add_argument("--device", help="Wi-Fi device (default: the first one)")
    p_net.add_argument("--open", action="store_true", help="Open network: do not ask for a password")
    p_net.set_defaults(func=cmd_network)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
