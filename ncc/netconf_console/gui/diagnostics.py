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
        from tkinter import ttk
        style = ttk.Style(window.root)
        check("Separate coloured XML edit actions", window.send_button.cget("text") == "NETCONF方式修改"
              and window.admin_edit_button.cget("text") == "使用系統sysrepocfg修改"
              and window.send_button.master is window.admin_edit_button.master
              and style.lookup("Netconf.TButton", "background", ()) == "#15803d"
              and style.lookup("Sysrepo.TButton", "background", ()) == "#b91c1c")
        check("High contrast connection tabs", window.auth_tabs.cget("style") == "Connection.TNotebook"
              and style.lookup("Connection.TNotebook.Tab", "background", ("selected",)) == "#0969da")
        check("System SSH host key verification defaults off",
              not window.vars["admin_verify"].get()
              and window.admin_connect_button.cget("background") == "#0969da")
        check("SSH jump tab label and running source default",
              "SSH跳板" in [window.auth_tabs.tab(tab, "text") for tab in window.auth_tabs.tabs()]
              and window.vars["source"].get() == "running")
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
        from . import backups, safety, events
        check("Jump settings and disabled explanations", not window.vars["jump_enabled"].get()
              and window.vars["jump_verify"].get() and bool(window.action_reason("draft")))
        check("Independent system SSH tab", window.vars["admin_port"].get() == "22"
              and window.admin_connection is None and window.admin_edit_button.instate(["disabled"]))
        check("Draft RPC is test-only", safety.draft_rpc(window.plan)[0].findtext("{%s}test-option" % NC) == "test-only")
        with tempfile.TemporaryDirectory(prefix="netconf-config-backup-test-") as directory:
            from .client import ReadOptions
            path = Path(directory) / "snapshot.nccbackup"
            backups.save_backup(path, window.client.read(ReadOptions(defaults=True)), "synthetic-config-backup", window.client.schema)
            payload = backups.load_backup(path)
            check("Frozen DPAPI configuration backup", payload["device"] == "synthetic-config-backup"
                  and b"synthetic-config-backup" not in path.read_bytes())
            changes = backups.restore_choices(window.selection, payload["xml"], window.client.schema, "running")
            check("Frozen selective restore remains local", not changes)
        check("Notification completion parser", events.event_record('<notification xmlns="%s"><notificationComplete/></notification>' % events.NOTIFICATION_NS).completed)
        result = window.client.apply(window.selection, window.plan, window.snapshot.options)
        check("Offline edit round-trip", result.snapshot.data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text == "9000")
        try:
            build_plan(window.selection, old.replace(">up<", ">down<"), window.client.schema)
        except EditError:
            check("State writes blocked", True)
        else:
            check("State writes blocked", False)
        from .creation import Template, new_root_selection
        from .creation_ui import CreationDialog
        from .schema import SchemaIndex
        from .client import Snapshot, ReadOptions
        schema = SchemaIndex.compile({"create-test": 'module create-test {namespace "urn:create-test";prefix t;container absent {presence "enabled";}}'})
        window.client.schema = schema
        snapshot = Snapshot(etree.Element("data"), ReadOptions())
        window._accept_snapshot(snapshot)
        dialog = CreationDialog(window, snapshot.data, (), schema.lookup(("{urn:create-test}absent",)))
        window.root.update()
        check("Frozen schema creation form", dialog.template is not None and not dialog.template.issues())
        dialog.stage()
        check("Absent root stages a create RPC without touching snapshot", window.plan is not None
              and window.plan.rpc is not None and 'operation="create"' in window.plan.wire_xml
              and not window.selection.exists and window.dirty and len(snapshot.data) == 0)
        window.vars["show_candidates"].set(True)
        draft = window.editor.get()
        window._refresh_creation_candidates()
        check("Candidate hints preserve draft", bool(window.creation_candidates) and draft == window.editor.get())
        report["passed"] = True
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report


def sysrepo_loopback_test(filename):
    """Do not use on real devices. Both address and explicit peer marker are checked."""
    from ..session import ConnectionSettings
    from . import sysrepo
    from .client import ReadOptions
    from .demo import DemoClient
    from .model import Selection
    report = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)), "passed": False}
    shell = None
    try:
        settings = ConnectionSettings(**json.loads(Path(filename).read_text(encoding="utf-8")))
        if settings.host not in {"127.0.0.1", "::1"} or settings.call_home or settings.jump_enabled or not 0 < settings.timeout <= 30:
            raise ValueError("Only direct numeric loopback SSH is allowed for this diagnostic.")
        shell = sysrepo.ShellConnection(settings)
        shell.connect()
        marker, _ = shell.run("sysrepocfg --version", timeout=5)
        if marker.strip() != b"ncc-synthetic-sysrepo-peer":
            raise ValueError("Not the marked synthetic sysrepo test peer. No edit sent.")
        demo = DemoClient()
        snapshot = demo.read(ReadOptions(defaults=True))
        selection = Selection(snapshot.data[0][0], (snapshot.data[0],))
        plan = build_plan(selection, selection.text().replace(">1500<", ">9000<"), demo.schema)
        prepared = sysrepo.prepare(plan, demo.schema, timeout=1, defaults=True)
        report["payload"] = prepared.payload.decode("utf-8")
        report["command"] = prepared.command
        reply, warnings = sysrepo.execute(shell, prepared, selection, plan, demo.schema, timeout=1, defaults=True)
        if warnings:
            raise AssertionError(warnings)
        report["passed"] = "exit=0" in reply
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if shell:
            shell.close()
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
        if settings.jump_enabled and settings.jump_host not in {"127.0.0.1", "::1"}:
            raise ValueError("Diagnostic jump host must also be numeric loopback.")
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
        from . import safety, events
        test_rpc = safety.draft_rpc(plan)
        safety.test_draft(client, selection, plan, options, test_rpc)
        if client.read(options).data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text != "1500":
            raise AssertionError("test-only changed the datastore")
        from ncclient.xml_ import to_xml
        report["test_xml"] = to_xml(test_rpc)
        report["checks"].append("Draft test-only leaves running unchanged")
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
        candidate_options = ReadOptions("candidate", True, False)
        candidate = client.read(candidate_options)
        interface = candidate.data.find(".//{urn:ietf:params:xml:ns:yang:ietf-interfaces}interface")
        selection = Selection(interface, (interface.getparent(),))
        candidate_plan = build_plan(selection, selection.text().replace(">1500<", ">1600<"), client.schema, "candidate")
        client.apply(selection, candidate_plan, candidate_options)
        report["candidate_edit_xml"] = candidate_plan.wire_xml
        for confirm in (False, True):
            prepared = prepare(client, "confirmed", confirm_timeout=30)
            reply, warnings = execute(client, prepared)
            if warnings:
                raise AssertionError(warnings)
            if client.read(ReadOptions(defaults=True)).data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text != "1600":
                raise AssertionError("Confirmed commit did not update running")
            report["lifecycle_xml"].append(prepared.wire_xml)
            rpc = safety.pending_rpc(client.pending_commit, confirm)
            reply, warnings = safety.finish_pending(client, client.pending_commit, confirm, rpc)
            if warnings:
                raise AssertionError(warnings)
            expected = "1600" if confirm else "1500"
            if client.read(ReadOptions(defaults=True)).data.find(".//{urn:o-ran:interfaces:1.0}l2-mtu").text != expected:
                raise AssertionError("Confirmed cancel/confirm readback mismatch")
            report["lifecycle_xml"].append(to_xml(rpc))
        report["checks"].append("Confirmed commit with token-bound explicit cancel and confirmation")
        from .creation import Template, new_root_selection
        template = Template(client.schema, client.schema.lookup(("{urn:ietf:params:xml:ns:yang:ietf-hardware}demo-note",)))
        template.set_value(template.root[0], "synthetic create round-trip")
        creation_selection = new_root_selection(template.root, client.schema)
        creation_plan = build_plan(creation_selection, etree.tostring(template.root).decode(), client.schema)
        create_options = ReadOptions(defaults=True)
        creation_test = safety.draft_rpc(creation_plan)
        safety.test_draft(client, creation_selection, creation_plan, create_options, creation_test)
        if client.read(create_options).data.find(template.root.tag) is not None:
            raise AssertionError("Root creation test-only changed running")
        result = client.apply(creation_selection, creation_plan, create_options)
        if result.snapshot is None or result.snapshot.data.find(template.root.tag) is None:
            raise AssertionError("Created root missing from readback")
        report["creation_xml"] = creation_plan.wire_xml
        report["creation_test_xml"] = to_xml(creation_test)
        report["checks"].append("Absent-root test-only and explicit create round-trip")
        streams = events.discover_streams(client.manager)
        options = events.subscription_options("NETCONF", streams, '<alarm xmlns="urn:fixture"/>', "2026-01-01T00:00:00Z")
        client.manager.create_subscription(**options)
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
