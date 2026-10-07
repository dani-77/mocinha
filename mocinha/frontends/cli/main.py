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


def cmd_plan(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    try:
        manifest = Manifest.load_from_file(manifest_path)
    except Exception as e:
        print(f"Error loading manifest: {e}", file=sys.stderr)
        return 1

    stream = EventStream()
    if args.verbose:
        stream.subscribe(print_event)

    probe = SystemProbe(stream)
    facts = probe.probe_facts()

    registry = create_default_registry(stream)
    resolver = InstallationResolver(facts, manifest, registry, stream)

    choices = UserChoices(
        target_disk=args.disk,
        bootloader=args.bootloader or manifest.boot.default,
        username=args.user,
        password=args.password or "secret",
        hostname=args.hostname,
        selected_services=set(args.services.split(",")) if args.services else set(),
    )

    try:
        plan = resolver.resolve(choices)
        wire_plan_providers(plan, registry, manifest)
        print("\n" + plan.to_human_readable() + "\n")
        return 0
    except Exception as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1


def cmd_install(args: argparse.Namespace) -> int:
    from mocinha.core.executor import InstallationExecutor
    from mocinha.core.provider import ExecutionContext

    manifest_path = Path(args.manifest)
    try:
        manifest = Manifest.load_from_file(manifest_path)
    except Exception as e:
        print(f"Error loading manifest: {e}", file=sys.stderr)
        return 1

    stream = EventStream()
    stream.subscribe(print_event)

    probe = SystemProbe(stream)
    facts = probe.probe_facts()

    registry = create_default_registry(stream)
    resolver = InstallationResolver(facts, manifest, registry, stream)

    choices = UserChoices(
        target_disk=args.disk,
        bootloader=args.bootloader or manifest.boot.default,
        username=args.user,
        password=args.password or "secret",
        hostname=args.hostname,
        selected_services=set(args.services.split(",")) if args.services else set(),
    )

    try:
        plan = resolver.resolve(choices)
        wire_plan_providers(plan, registry, manifest)
        print("\n" + plan.to_human_readable() + "\n")
    except Exception as e:
        print(f"Resolution failed:\n{e}", file=sys.stderr)
        return 1

    if not args.confirm:
        print("To proceed with destructive execution, pass --confirm.", file=sys.stderr)
        return 1

    context = ExecutionContext(
        target_disk=plan.summary.disk,
        target_mount=args.mount,
        metadata={
            "username": plan.summary.username,
            "password": args.password or "secret",
            "hostname": plan.summary.hostname,
            "firmware": plan.summary.firmware,
            "enabled_services": plan.summary.services,
            "live_only_to_clean": plan.summary.live_only_removed,
            "install_source": manifest.install.source,
        },
    )

    executor = InstallationExecutor(stream)
    try:
        executor.execute_plan(plan, context, confirmed=True)
        print("\n✓ Installation completed and verified successfully.\n")
        return 0
    except Exception as e:
        print(f"\n✗ Installation failed:\n{e}\n", file=sys.stderr)
        return 1


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
    p_plan.add_argument("--user", default="user", help="Primary user account name")
    p_plan.add_argument("--password", default="", help="Password for user")
    p_plan.add_argument("--hostname", default="mocinha", help="Target hostname")
    p_plan.add_argument("--services", help="Comma-separated requested services")
    p_plan.set_defaults(func=cmd_plan)

    # install
    p_inst = subparsers.add_parser("install", help="Execute approved installation plan")
    p_inst.add_argument("--manifest", required=True, help="Path to manifest file")
    p_inst.add_argument("--disk", required=True, help="Target disk path (e.g. /dev/vda)")
    p_inst.add_argument("--bootloader", help="Requested bootloader (e.g. limine)")
    p_inst.add_argument("--user", default="user", help="Primary user account name")
    p_inst.add_argument("--password", default="secret", help="Password for user")
    p_inst.add_argument("--hostname", default="mocinha", help="Target hostname")
    p_inst.add_argument("--services", help="Comma-separated requested services")
    p_inst.add_argument("--mount", default="/mnt", help="Staging mount directory (default /mnt)")
    p_inst.add_argument("--confirm", action="store_true", help="Confirm destructive disk modification")
    p_inst.set_defaults(func=cmd_install)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
