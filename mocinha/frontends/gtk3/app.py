"""Mocinha GTK3 Frontend.

Strict architectural boundary:
Consumes mocinha.core via public API only.
GTK objects and widgets NEVER cross into the engine.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from mocinha.core.errors import ExecutionError, ResolutionError
from mocinha.core.events import Event, EventPhase, EventStream
from mocinha.core.executor import InstallationExecutor
from mocinha.core.manifest import Manifest
from mocinha.core.plan import InstallationPlan, PlanStep
from mocinha.core.probe import DiskDevice, SystemFacts, SystemProbe
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.core.services import ServiceCategory
from mocinha.providers import create_default_registry


class MocinhaGTKApp(Gtk.Window):
    """Main installer window with multi-step wizard navigation."""

    def __init__(self, manifest: Manifest) -> None:
        super().__init__(title=f"Mocinha Installer --- {manifest.system.name}")
        self.set_default_size(860, 600)
        self.set_position(Gtk.WindowPosition.CENTER)

        self.manifest = manifest
        self.events = EventStream()
        self.registry = create_default_registry(self.events)
        self.facts = SystemProbe(self.events).probe_facts()
        self.resolver = InstallationResolver(self.facts, self.manifest, self.registry, self.events)
        self.executor = InstallationExecutor(self.events)

        self.resolved_plan: Optional[InstallationPlan] = None
        self.current_step_index = 0

        # Subscribe event stream to details log view
        self.events.subscribe(self._on_event)

        # Build UI layout
        self._init_ui()

    def _init_ui(self) -> None:
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # Header bar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(self.manifest.system.name)
        header.set_subtitle("Knowledgeable, not opinionated")
        self.set_titlebar(header)

        # Content stack
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(250)
        main_box.pack_start(self.stack, True, True, 0)

        # Create pages
        self._page_welcome = self._create_welcome_page()
        self._page_disk = self._create_disk_page()
        self._page_user = self._create_user_page()
        self._page_services = self._create_services_page()
        self._page_boot = self._create_boot_page()
        self._page_summary = self._create_summary_page()
        self._page_progress = self._create_progress_page()
        self._page_finish = self._create_finish_page()

        self.pages = [
            ("welcome", self._page_welcome),
            ("disk", self._page_disk),
            ("user", self._page_user),
            ("services", self._page_services),
            ("boot", self._page_boot),
            ("summary", self._page_summary),
            ("progress", self._page_progress),
            ("finish", self._page_finish),
        ]

        for name, widget in self.pages:
            self.stack.add_named(widget, name)

        # Bottom navigation bar
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        nav_box.set_margin_top(12)
        nav_box.set_margin_bottom(12)
        nav_box.set_margin_start(16)
        nav_box.set_margin_end(16)

        self.btn_back = Gtk.Button(label="Back")
        self.btn_back.connect("clicked", self._on_back_clicked)
        self.btn_back.set_sensitive(False)
        nav_box.pack_start(self.btn_back, False, False, 0)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_hexpand(True)
        self.status_label.set_alignment(0.5, 0.5)
        nav_box.pack_start(self.status_label, True, True, 0)

        self.btn_next = Gtk.Button(label="Next")
        self.btn_next.get_style_context().add_class("suggested-action")
        self.btn_next.connect("clicked", self._on_next_clicked)
        nav_box.pack_end(self.btn_next, False, False, 0)

        main_box.pack_end(nav_box, False, False, 0)

    # -------------------------------------------------------------
    # Page Builders
    # -------------------------------------------------------------

    def _create_welcome_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_top(40)
        box.set_margin_bottom(40)
        box.set_margin_start(40)
        box.set_margin_end(40)

        # Mascot / Logo
        logo_path = Path(__file__).parent.parent.parent.parent / "mocinha-logo-mascot.png"
        if logo_path.is_file():
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_path), 160, 160, True)
                image = Gtk.Image.new_from_pixbuf(pb)
                box.pack_start(image, False, False, 0)
            except Exception:
                pass

        title = Gtk.Label()
        title.set_markup(f"<span size='xx-large' weight='bold'>Welcome to {self.manifest.system.name}</span>")
        box.pack_start(title, False, False, 0)

        desc = Gtk.Label()
        desc.set_line_wrap(True)
        desc.set_markup(
            "Mocinha will install this live system directly to your target disk.\n\n"
            "• <b>Offline-first:</b> No Internet downloads required.\n"
            "• <b>Transparent:</b> Review a complete plan before any disk is modified.\n"
            "• <b>Knowledgeable:</b> Validates system constraints and dependencies."
        )
        box.pack_start(desc, False, False, 0)
        return box

    def _create_disk_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(30)
        box.set_margin_bottom(30)
        box.set_margin_start(40)
        box.set_margin_end(40)

        lbl = Gtk.Label()
        lbl.set_markup("<span size='x-large' weight='bold'>Select Target Disk</span>")
        lbl.set_alignment(0, 0.5)
        box.pack_start(lbl, False, False, 0)

        self.disk_store = Gtk.ListStore(str, str, str)  # dev_path, summary_text, raw_dev
        for d in self.facts.disks:
            flags = []
            if d.read_only:
                flags.append("Read-Only")
            if d.removable:
                flags.append("Removable")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            desc = f"{d.path} — {d.size_gib} GiB ({d.model}){flag_str}"
            self.disk_store.append([d.path, desc, d.path])

        self.disk_combo = Gtk.ComboBox.new_with_model(self.disk_store)
        renderer = Gtk.CellRendererText()
        self.disk_combo.pack_start(renderer, True)
        self.disk_combo.add_attribute(renderer, "text", 1)
        if len(self.disk_store) > 0:
            self.disk_combo.set_active(0)
        box.pack_start(self.disk_combo, False, False, 0)

        warn_lbl = Gtk.Label()
        warn_lbl.set_markup(
            "<span color='#d9534f'><b>Warning:</b> The selected disk will be completely partitioned and formatted. "
            "All existing data on it will be destroyed upon confirmation.</span>"
        )
        warn_lbl.set_line_wrap(True)
        warn_lbl.set_alignment(0, 0.5)
        box.pack_start(warn_lbl, False, False, 0)
        return box

    def _create_user_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(30)
        box.set_margin_bottom(30)
        box.set_margin_start(40)
        box.set_margin_end(40)

        lbl = Gtk.Label()
        lbl.set_markup("<span size='x-large' weight='bold'>User Account &amp; System Name</span>")
        lbl.set_alignment(0, 0.5)
        box.pack_start(lbl, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(12)

        def password_entry(placeholder: str) -> Gtk.Entry:
            entry = Gtk.Entry()
            entry.set_visibility(False)
            entry.set_placeholder_text(placeholder)
            return entry

        rows = [
            ("Username:", "entry_user", Gtk.Entry(placeholder_text="e.g. dani")),
            ("Password:", "entry_pass", password_entry("required")),
            ("Confirm password:", "entry_pass2", password_entry("repeat the password")),
            ("Root password:", "entry_root", password_entry("empty: root account locked")),
            ("Confirm root password:", "entry_root2", password_entry("repeat the root password")),
            ("Hostname:", "entry_host", Gtk.Entry(text=self.manifest.system.id)),
        ]
        for row, (label, attr, entry) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            setattr(self, attr, entry)
            grid.attach(entry, 1, row, 1, 1)

        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_markup(
            "<small>Root password: set one to use the root account, or leave it empty to keep root "
            "locked and administer the system as the user above (member of <b>wheel</b>). "
            "The plan summary shows which one applies.</small>"
        )
        grid.attach(note, 0, len(rows), 2, 1)
        box.pack_start(grid, False, False, 0)
        return box

    def _create_services_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(25)
        box.set_margin_bottom(25)
        box.set_margin_start(40)
        box.set_margin_end(40)

        lbl = Gtk.Label()
        lbl.set_markup("<span size='x-large' weight='bold'>Persistent Services</span>")
        lbl.set_alignment(0, 0.5)
        box.pack_start(lbl, False, False, 0)

        sub = Gtk.Label()
        sub.set_markup("Configure which system services will persist and run on subsequent boots.")
        sub.set_alignment(0, 0.5)
        box.pack_start(sub, False, False, 0)

        self.service_checkboxes: Dict[str, Gtk.CheckButton] = {}

        # Required services (locked)
        if self.manifest.services.required:
            box.pack_start(Gtk.Label(label="Required Services (Non-optional):", xalign=0), False, False, 0)
            for s in self.manifest.services.required:
                cb = Gtk.CheckButton(label=f"{s} (Required by system)")
                cb.set_active(True)
                cb.set_sensitive(False)
                self.service_checkboxes[s] = cb
                box.pack_start(cb, False, False, 0)

        # Default-enabled services
        if self.manifest.services.default_enabled:
            box.pack_start(Gtk.Label(label="Recommended Services:", xalign=0), False, False, 0)
            for s in self.manifest.services.default_enabled:
                cb = Gtk.CheckButton(label=f"{s} (Default)")
                cb.set_active(True)
                self.service_checkboxes[s] = cb
                box.pack_start(cb, False, False, 0)

        # Optional services
        if self.manifest.services.optional:
            box.pack_start(Gtk.Label(label="Optional Services:", xalign=0), False, False, 0)
            for s in self.manifest.services.optional:
                cb = Gtk.CheckButton(label=s)
                cb.set_active(False)
                self.service_checkboxes[s] = cb
                box.pack_start(cb, False, False, 0)

        return box

    def _create_boot_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(30)
        box.set_margin_bottom(30)
        box.set_margin_start(40)
        box.set_margin_end(40)

        lbl = Gtk.Label()
        lbl.set_markup("<span size='x-large' weight='bold'>Bootloader Selection</span>")
        lbl.set_alignment(0, 0.5)
        box.pack_start(lbl, False, False, 0)

        firmware_str = self.facts.firmware.value
        info = Gtk.Label()
        info.set_markup(f"Machine Firmware detected: <b>{firmware_str}</b>")
        info.set_alignment(0, 0.5)
        box.pack_start(info, False, False, 0)

        self.boot_combo = Gtk.ComboBoxText()
        for b in self.manifest.boot.available:
            self.boot_combo.append_text(b)
        # Select manifest default
        if self.manifest.boot.default in self.manifest.boot.available:
            self.boot_combo.set_active(self.manifest.boot.available.index(self.manifest.boot.default))
        elif len(self.manifest.boot.available) > 0:
            self.boot_combo.set_active(0)

        box.pack_start(self.boot_combo, False, False, 0)
        return box

    def _create_summary_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(30)
        box.set_margin_end(30)

        lbl = Gtk.Label()
        lbl.set_markup("<span size='x-large' weight='bold'>Installation Plan Summary</span>")
        lbl.set_alignment(0, 0.5)
        box.pack_start(lbl, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.summary_text_view = Gtk.TextView()
        self.summary_text_view.set_editable(False)
        self.summary_text_view.set_monospace(True)
        scroll.add(self.summary_text_view)
        box.pack_start(scroll, True, True, 0)
        return box

    def _create_progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(30)
        box.set_margin_end(30)

        self.progress_title = Gtk.Label()
        self.progress_title.set_markup("<span size='large' weight='bold'>Installing system...</span>")
        self.progress_title.set_alignment(0, 0.5)
        box.pack_start(self.progress_title, False, False, 0)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        box.pack_start(self.progress_bar, False, False, 0)

        # Details expander with real-time log
        expander = Gtk.Expander(label="Details (Execution Log)")
        expander.set_expanded(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.log_text_view = Gtk.TextView()
        self.log_text_view.set_editable(False)
        self.log_text_view.set_monospace(True)
        scroll.add(self.log_text_view)
        expander.add(scroll)
        box.pack_start(expander, True, True, 0)
        return box

    def _create_finish_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_top(60)
        box.set_margin_bottom(60)
        box.set_margin_start(40)
        box.set_margin_end(40)

        title = Gtk.Label()
        title.set_markup("<span size='xx-large' weight='bold' color='#5cb85c'>Installation Completed!</span>")
        box.pack_start(title, False, False, 0)

        desc = Gtk.Label()
        desc.set_markup("The live system has been successfully deployed and verified on your target disk.\n"
                        "You can now restart your computer to boot into your new installation.")
        box.pack_start(desc, False, False, 0)
        return box

    # -------------------------------------------------------------
    # Navigation and Execution
    # -------------------------------------------------------------

    def _on_back_clicked(self, widget: Gtk.Button) -> None:
        if self.current_step_index > 0:
            self.current_step_index -= 1
            name, _ = self.pages[self.current_step_index]
            self.stack.set_visible_child_name(name)
            self._update_nav_buttons()

    def _on_next_clicked(self, widget: Gtk.Button) -> None:
        current_name, _ = self.pages[self.current_step_index]

        if current_name == "user":
            problem = self._check_user_page()
            if problem:
                self._show_error_dialog("User Account", problem)
                return

        # Before entering Summary, resolve the plan!
        if current_name == "boot":
            try:
                self._resolve_and_update_summary()
            except ResolutionError as re:
                self._show_error_dialog("Resolution Error", str(re))
                return

        # On Summary page, clicking Next means CONFIRM INSTALLATION
        if current_name == "summary":
            confirm = self._confirm_destruction_dialog()
            if not confirm:
                return
            self._start_execution()
            return

        if current_name == "finish":
            self.destroy()
            return

        if self.current_step_index < len(self.pages) - 1:
            self.current_step_index += 1
            name, _ = self.pages[self.current_step_index]
            self.stack.set_visible_child_name(name)
            self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        current_name, _ = self.pages[self.current_step_index]
        self.btn_back.set_sensitive(self.current_step_index > 0 and current_name not in ("progress", "finish"))
        if current_name == "summary":
            self.btn_next.set_label("Install Now")
            self.btn_next.get_style_context().remove_class("suggested-action")
            self.btn_next.get_style_context().add_class("destructive-action")
        elif current_name == "finish":
            self.btn_next.set_label("Close")
            self.btn_next.set_sensitive(True)
        elif current_name == "progress":
            self.btn_next.set_sensitive(False)
        else:
            self.btn_next.set_label("Next")
            self.btn_next.get_style_context().remove_class("destructive-action")
            self.btn_next.get_style_context().add_class("suggested-action")

    def _resolve_and_update_summary(self) -> None:
        # Collect choices
        active_iter = self.disk_combo.get_active_iter()
        target_disk = self.disk_store[active_iter][0] if active_iter else "/dev/sda"
        bootloader = self.boot_combo.get_active_text() or self.manifest.boot.default
        username = self.entry_user.get_text()
        password = self.entry_pass.get_text()
        root_password = self.entry_root.get_text() or None
        hostname = self.entry_host.get_text()

        selected_srvs: Set[str] = set()
        for s_name, cb in self.service_checkboxes.items():
            if cb.get_active():
                selected_srvs.add(s_name)

        choices = UserChoices(
            target_disk=target_disk,
            bootloader=bootloader,
            username=username,
            password=password,
            root_password=root_password,
            hostname=hostname,
            selected_services=selected_srvs,
        )

        from mocinha.providers import wire_plan_providers

        plan = self.resolver.resolve(choices)
        # Wire before showing the summary so a missing provider is reported before confirmation
        wire_plan_providers(plan, self.registry, self.manifest)
        self.resolved_plan = plan
        buf = self.summary_text_view.get_buffer()
        buf.set_text(self.resolved_plan.to_human_readable())

    def _confirm_destruction_dialog(self) -> bool:
        disk = self.resolved_plan.summary.disk if self.resolved_plan else "target disk"
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Confirm destruction of {disk}?",
        )
        dialog.format_secondary_text(
            f"All existing data on {disk} will be erased.\n"
            "This action cannot be undone.\n\n"
            "Do you wish to proceed with the installation?"
        )
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _start_execution(self) -> None:
        self.current_step_index = 6  # Progress page
        self.stack.set_visible_child_name("progress")
        self._update_nav_buttons()

        context = ExecutionContext(
            target_disk=self.resolved_plan.summary.disk,
            target_mount="/mnt",
            metadata={
                **self.resolved_plan.metadata,
                "password": self.entry_pass.get_text(),
                "root_password": self.entry_root.get_text() or None,
            },
        )

        def worker() -> None:
            def progress_cb(current: int, total: int, step: PlanStep) -> None:
                fraction = current / total
                GLib.idle_add(self._update_progress, fraction, step.title)

            try:
                self.executor.execute_plan(
                    self.resolved_plan,
                    context,
                    confirmed=True,
                    progress_cb=progress_cb,
                )
                GLib.idle_add(self._on_execution_success)
            except Exception as e:
                GLib.idle_add(self._on_execution_error, str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _update_progress(self, fraction: float, title: str) -> bool:
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)}% - {title}")
        return False

    def _on_execution_success(self) -> bool:
        self.current_step_index = 7  # Finish page
        self.stack.set_visible_child_name("finish")
        self._update_nav_buttons()
        return False

    def _on_execution_error(self, err_msg: str) -> bool:
        self.progress_title.set_markup(f"<span color='#d9534f' weight='bold'>Installation failed:</span> {err_msg}")
        self._show_error_dialog("Execution Error", err_msg)
        return False

    def _on_event(self, event: Event) -> None:
        log_line = event.format_log_line() + "\n"
        GLib.idle_add(self._append_log, log_line)

    def _append_log(self, text: str) -> bool:
        buf = self.log_text_view.get_buffer()
        end_iter = buf.get_end_iter()
        buf.insert(end_iter, text)
        return False

    def _check_user_page(self) -> Optional[str]:
        """Local form checks; the resolver validates the values themselves."""
        if not self.entry_user.get_text():
            return "Enter a user name."
        if not self.entry_pass.get_text():
            return "Enter a password for the user."
        if self.entry_pass.get_text() != self.entry_pass2.get_text():
            return "The user passwords do not match."
        if self.entry_root.get_text() != self.entry_root2.get_text():
            return "The root passwords do not match."
        return None

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()


# Where a live system ships its manifest (docs/manifest-schema.md)
MANIFEST_LOCATIONS = (Path("/etc/mocinha.toml"), Path("/usr/share/mocinha/mocinha.toml"))


def run_gtk_app(manifest_path: Optional[str] = None) -> int:
    path = Path(manifest_path) if manifest_path else next((p for p in MANIFEST_LOCATIONS if p.is_file()), None)
    if path is None:
        print(
            "No manifest given and none found in the live system "
            f"({', '.join(str(p) for p in MANIFEST_LOCATIONS)}). Pass the remaster manifest path.",
            file=sys.stderr,
        )
        return 1
    try:
        manifest = Manifest.load_from_file(path)
    except Exception as e:
        print(f"Failed to load manifest: {e}", file=sys.stderr)
        return 1

    app = MocinhaGTKApp(manifest)
    app.connect("destroy", Gtk.main_quit)
    app.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    m_path = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run_gtk_app(m_path))
