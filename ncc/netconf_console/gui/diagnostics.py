"""Packaged GUI diagnostics: native widgets, schema, edits and colours, offline."""

from __future__ import annotations

import sys
import json
from pathlib import Path

from lxml import etree

from . import VERSION
from .model import NC, WD, EditError, build_plan


def self_test(window):
    report = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "checks": [], "passed": False}
    def check(name, value):
        if not value:
            raise AssertionError(name)
        report["checks"].append(name)
    try:
        check("SSH/TLS name verification defaults off", not window.vars["hostkey_verify"].get()
              and not window.vars["verify_hostname"].get())
        check("Pretty button", window.format_button.cget("text") == "Pretty")
        check("XML export labels and empty-tree guard", window.save_button.cget("text") == "匯出XML"
              and window.save_tree_button.cget("text") == "匯出XML"
              and window.save_tree_button.instate(["disabled"]))
        import tempfile
        from .preferences import PreferencesStore, empty_book, remember_account, remember_connection
        with tempfile.TemporaryDirectory(prefix="netconf-gui-preferences-test-") as directory:
            store = PreferencesStore(Path(directory) / "settings.dpapi")
            book = empty_book()
            fixture = {"mode": "Direct SSH", "host": "fixture.invalid", "port": "830",
                       "username": "fixture-user", "password": "synthetic-DPAPI-secret-測試"}
            remember_account(book, fixture)
            remember_connection(book, fixture)
            store.save(book)
            check("Windows DPAPI encrypted credential round-trip", PreferencesStore(store.path).load() == book
                  and fixture["password"].encode() not in store.path.read_bytes())
        from .profiles import ProfileManager
        window.preferences = empty_book()
        remember_connection(window.preferences, fixture, "Diagnostic profile")
        dialog = ProfileManager(window, "connections")
        window.root.update()
        check("Native profile management list", dialog.list.size() == 1)
        check("Profile rename/delete without credential loss",
              window.change_profile_catalog("connections", ["Diagnostic profile"], "Renamed")
              and window.preferences["connections"]["Renamed"]["password"] == fixture["password"]
              and window.change_profile_catalog("connections", ["Renamed"])
              and not window.preferences["connections"])
        dialog.window.destroy()
        window.load_demo()
        window.root.update()
        check("Native Tk/ttk window", bool(window.root.winfo_id()))
        from .windows import icon_path, has_window_icons
        check("Bundled native big/small window icons", icon_path().is_file() and has_window_icons(window.root))
        check("Auto reconnect defaults off", not window.vars["auto_reconnect"].get())
        window.toggle_connection()
        window.root.update()
        check("Connection panel can collapse", not window.connection_panel.winfo_ismapped())
        window.toggle_connection()
        window.root.update()
        check("Connection panel can reopen", bool(window.connection_panel.winfo_ismapped()))
        check("Source defaults to running", window.vars["source"].get() == "running")
        check("Independent schema compiler", window.client.schema.complete and len(window.client.schema.nodes) >= 15)
        check("Root tree", len(window.tree.get_children()) == 2)
        check("Loaded-tree export enabled", not window.save_tree_button.instate(["disabled"]))
        window._expand_item("0")
        window._show_selection("0/0")
        window.root.update()
        check("Default highlighting", bool(window.editor.text.tag_ranges("default")))
        check("config false highlighting", bool(window.editor.text.tag_ranges("state")))
        old = window.editor.get()
        window.editor.set(old.replace(">1500<", ">9000<"))
        window._update_preview()
        window.root.update()
        check("Live preview and edit highlighting", window.plan is not None and bool(window.editor.text.tag_ranges("changed")))
        check("List key included", ">eth0<" in window.preview.get())
        check("Read-only/default metadata excluded from edit", "oper-status" not in window.preview.get() and ("{%s}default" % WD) not in [key for node in window.plan.rpc.iter() for key in node.attrib])
        check("Exact outgoing envelope", etree.fromstring(window.preview.get().encode()).tag == "{%s}rpc" % NC)
        check("Instance-aware old/new diff", "原值: 1500" in window.diff_pane.get()
              and "新值: 9000" in window.diff_pane.get() and "name='eth0'" in window.diff_pane.get())
        from .workspace import search_snapshot, import_selection
        check("Search includes unexpanded nodes", bool(search_snapshot(window.snapshot.data, window.client.schema, "port-number")))
        imported = import_selection(etree.tostring(window.snapshot.data), window.selection, window.client.schema)
        check("Tree XML import remains a local no-op", build_plan(window.selection, etree.tostring(imported).decode(), window.client.schema).rpc is None)
        check("Independent private-key secret and mode", window.vars["ssh_auth"].get() == "auto"
              and window.vars["key_passphrase"].get() == "")
        check("Datastore controls fail closed offline", window.datastore_menu.entrycget(0, "state") == "disabled")
        result = window.client.apply(window.selection, window.plan, window.snapshot.options)
        check("Offline edit round-trip", result.snapshot.data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text == "9000")
        try:
            build_plan(window.selection, old.replace(">up<", ">down<"), window.client.schema)
        except EditError:
            check("State writes blocked", True)
        else:
            check("State writes blocked", False)
        report["passed"] = True
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report


def loopback_test(filename):
    """Packaged transport regression, restricted to an explicitly marked local peer."""
    from ..session import ConnectionSettings
    from .client import GuiClient, ReadOptions
    from .model import Selection
    report = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "checks": [], "passed": False}
    client = GuiClient()
    try:
        values = json.loads(Path(filename).read_text(encoding="utf-8"))
        settings = ConnectionSettings(**values)
        endpoint = settings.listen_host if settings.call_home else settings.host
        if endpoint not in {"127.0.0.1", "::1"} or settings.timeout <= 0 or settings.timeout > 30:
            raise ValueError("Diagnostic connections require a numeric loopback address and 1–30 second timeout.")
        client.connect(settings)
        if "urn:netconf-console2:gui-test-peer:1.0" not in client.capabilities:
            raise ValueError("This is not the explicitly marked NETCONF GUI test peer.")
        report["checks"].append("Frozen transport handshake and NETCONF hello")
        client.load_schema(force=True)
        if not client.schema.complete:
            raise AssertionError(client.schema.warnings)
        report["checks"].append("Device get-schema compilation")
        options = ReadOptions("running", True, True)
        snapshot = client.read(options)
        interface = snapshot.data.find(".//{urn:ietf:params:xml:ns:yang:ietf-interfaces}interface")
        if interface is None:
            raise AssertionError("Missing fixture data")
        selection = Selection(interface, (interface.getparent(),))
        plan = build_plan(selection, selection.text().replace(">1500<", ">9000<"), client.schema)
        report["checks"].append("Running + defaults + config false retrieval")
        result = client.apply(selection, plan, options)
        if result.snapshot is None or result.snapshot.data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text != "9000":
            raise AssertionError("Edited value did not round trip")
        report["wire_xml"] = plan.wire_xml
        report["checks"].append("Lock, conflict reread, exact preview edit, unlock, reread")
        from .lifecycle import prepare, execute
        report["lifecycle_xml"] = []
        for operation in ("save", "validate", "commit", "discard"):
            prepared = prepare(client, operation)
            reply, warnings = execute(client, prepared)
            if warnings or "ok" not in reply:
                raise AssertionError((operation, warnings))
            report["lifecycle_xml"].append(prepared.wire_xml)
        report["checks"].append("Explicit startup save, candidate commit/discard and datastore validation")
        client.manager.create_subscription(stream_name="NETCONF")
        notification = client.manager.take_notification(timeout=5)
        if notification is None or "minor" not in notification.notification_xml:
            raise AssertionError("Missing fixture notification")
        report["checks"].append("RFC5277 subscription and asynchronous notification")
        report["passed"] = True
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        client.disconnect()
    return report
