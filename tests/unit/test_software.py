"""Software Update manifest and package source helpers."""

from pathlib import Path
import tempfile
import unittest
import zipfile

from netconf_console.gui.model import EditError
from netconf_console.gui.software import (
    choose_manifest_build,
    package_file_name,
    read_selected_manifest,
)


MANIFEST = """<manifest><builds>
<build bldName="cobra" bldVersion="1.2.1" id="cobra-121">
  <file fileName="cobra_sdk-1.2.1.zip"/>
  <file fileName="manifest.xml"/>
</build>
</builds></manifest>"""


class SoftwareManifestTests(unittest.TestCase):
    def test_reads_direct_manifest_and_package_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.xml"
            package = root / "cobra_sdk-1.2.1.zip"
            manifest.write_text(MANIFEST, encoding="utf-8")
            package.write_bytes(b"package")

            source, text = read_selected_manifest([str(package), str(manifest)])

            self.assertEqual(source, "manifest.xml")
            self.assertEqual(package_file_name([str(package), str(manifest)]), package.name)
            self.assertEqual(choose_manifest_build([{
                "name": "cobra", "version": "1.2.1", "id": "cobra-121",
                "files": [package.name, "manifest.xml"],
            }], [package.name])["version"], "1.2.1")
            self.assertIn("bldVersion=\"1.2.1\"", text)

    def test_reads_manifest_inside_selected_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "cobra_sdk-1.2.1.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("metadata/manifest.xml", MANIFEST)

            source, text = read_selected_manifest([str(package)])

            self.assertEqual(source, "cobra_sdk-1.2.1.zip!/metadata/manifest.xml")
            self.assertEqual(text, MANIFEST)

    def test_ambiguous_manifest_build_requires_manual_choice(self):
        builds = [
            {"name": "a", "version": "1", "id": "a", "files": ["a.zip"]},
            {"name": "b", "version": "2", "id": "b", "files": ["b.zip"]},
        ]
        with self.assertRaises(EditError):
            choose_manifest_build(builds, ["other.zip"])


if __name__ == "__main__":
    unittest.main()
