"""Remote Sysrepo backup/restore script and marker safety tests."""
import unittest

from netconf_console.gui import system_backup
from netconf_console.gui.model import EditError


class SystemBackupTests(unittest.TestCase):
    def setUp(self):
        self.request = system_backup.prepare()

    def test_backup_script_is_timestamped_private_and_covers_selected_datastores(self):
        script = system_backup.backup_script(self.request)
        self.assertIn("umask 077", script)
        self.assertIn('mkdir -p "$BASE"', script)
        self.assertIn('chmod 700 "$BASE"', script)
        self.assertIn('STAMP=$(date -u +%Y%m%d-%H%M%S)', script)
        self.assertIn('DEST="$BASE/$STAMP"', script)
        self.assertIn('chmod 700 "$DEST"', script)
        self.assertIn("sysrepoctl -l | grep -F -q -- o-ran-sync", script)
        self.assertIn('sysrepocfg --export="$DEST/running.xml" --datastore=running --format=xml', script)
        self.assertIn('sysrepoctl -l > "$DEST/modules.txt"', script)
        self.assertIn('(cd "$DEST" && sha256sum ./*.xml > SHA256SUMS)', script)
        self.assertIn("printf 'NCC_BACKUP_DIR=%s\\n'", script)
        self.assertNotIn("%%s", script)

        selected = system_backup.prepare(datastores=("running", "candidate", "startup"))
        script = system_backup.backup_script(selected)
        for datastore in ("running", "candidate", "startup"):
            self.assertIn('--datastore=%s --format=xml' % datastore, script)

    def test_initialization_and_latest_scripts_are_read_only_checks(self):
        init = system_backup.initialization_script(self.request)
        latest = system_backup.latest_script(self.request)
        self.assertIn("sysrepoctl -l | grep -F -q -- o-ran-sync", init)
        self.assertIn("NCC_YANG_INITIALIZED=", init)
        self.assertIn("find \"$BASE\" -mindepth 1 -maxdepth 1 -type d", latest)
        self.assertIn("-name '20??????-??????*'", latest)
        self.assertIn("sort | tail -n 1", latest)
        self.assertIn("sha256sum -c SHA256SUMS", latest)
        self.assertIn("NCC_LATEST_BACKUP_DIR=%s", latest)
        self.assertNotIn("systemctl", latest)
        self.assertNotIn("--copy-from", latest)

    def test_restore_rechecks_latest_checksum_and_recovers_active_services(self):
        expected = "/data/backup-yang-baseline/20260913-120000"
        script = system_backup.restore_script(self.request, expected)
        self.assertIn('EXPECTED=/data/backup-yang-baseline/20260913-120000', script)
        self.assertIn('[ "$LATEST" != "$EXPECTED" ]', script)
        self.assertIn("-name '20??????-??????*'", script)
        self.assertIn('(cd "$LATEST" && sha256sum -c SHA256SUMS)', script)
        self.assertIn('sysrepocfg --copy-from="$LATEST/running.xml" --datastore=running --format=xml', script)
        for service in system_backup.STOP_SERVICES:
            self.assertIn('stop_one %s' % service, script)
        for service in system_backup.START_SERVICES:
            self.assertIn(service, script)
        self.assertIn("systemctl is-active --quiet", script)
        self.assertIn("NCC_RESTORE_APPLIED=%s", script)
        self.assertNotIn("sudo", script)
        self.assertNotIn("docker", script)

    def test_paths_executables_modules_and_datastores_reject_shell_input(self):
        for base in ("relative/path", "/data/../backup", "/data/backup;id", "/data/backup name"):
            with self.subTest(base=base), self.assertRaises(EditError):
                system_backup.prepare(base=base)
        for program, expected in (("sysrepocfg;id", "sysrepocfg"),
                                  ("sudo sysrepocfg", "sysrepocfg"),
                                  ("/bin/sysrepoctl -l", "sysrepoctl")):
            with self.subTest(program=program), self.assertRaises(EditError):
                if expected == "sysrepocfg":
                    system_backup.prepare(sysrepocfg=program)
                else:
                    system_backup.prepare(sysrepoctl=program)
        with self.assertRaises(EditError):
            system_backup.prepare(init_module="o-ran-sync;id")
        with self.assertRaises(EditError):
            system_backup.prepare(datastores=("candidate",))

    def test_marker_is_validated_and_collision_suffix_is_allowed(self):
        raw = b"NCC_LATEST_BACKUP_DIR=/data/backup-yang-baseline/20260913-120000-42\n"
        self.assertEqual(system_backup.parse_marker(raw, "NCC_LATEST_BACKUP_DIR", self.request.base),
                         "/data/backup-yang-baseline/20260913-120000-42")
        self.assertEqual(system_backup.marker_value(b"NCC_BACKUP_DATASTORES=running,candidate\n",
                                                    "NCC_BACKUP_DATASTORES"), "running,candidate")
        with self.assertRaises(EditError):
            system_backup.parse_marker(b"NCC_LATEST_BACKUP_DIR=/tmp/20260913-120000\n",
                                       "NCC_LATEST_BACKUP_DIR", self.request.base)
        with self.assertRaises(EditError):
            system_backup.parse_marker(b"no marker\n", "NCC_LATEST_BACKUP_DIR", self.request.base)


if __name__ == "__main__":
    unittest.main()
