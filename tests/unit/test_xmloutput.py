from __future__ import annotations

import codecs
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lxml import etree
from ncclient.operations.rpc import RPCError

from netconf_console import ncc, operations
from netconf_console.xmloutput import read_xml, serialize_xml


SAMPLE = "<data xmlns='urn:test'><interface><name>乙太網路</name><enabled>true</enabled></interface></data>"


class XmlOutputTests(unittest.TestCase):
    def test_pretty_preserves_values_attributes_and_does_not_mutate_reply(self):
        root = etree.fromstring((
            '<data xmlns="urn:test" xmlns:wd="urn:defaults">\n'
            ' <interface><name> 乙太網路 </name><enabled wd:default="true">true</enabled>'
            '<blank>   </blank><description>line1\n  line2</description></interface>\n</data>'
        ).encode("utf-8"))
        before = etree.tostring(root)
        rendered = serialize_xml(root)
        self.assertIn(b'\n  <interface>\n    <name>', rendered)
        self.assertEqual(etree.tostring(root), before)
        decoded = etree.fromstring(rendered)
        for original, formatted in zip(root.iter(), decoded.iter()):
            self.assertEqual(original.tag, formatted.tag)
            self.assertEqual(original.attrib, formatted.attrib)
            if not len(original):
                self.assertEqual(original.text, formatted.text)

    def test_pretty_preserves_mixed_content_and_xml_space(self):
        root = etree.fromstring(
            b'<data><message> <b>word</b> text <i>tail</i> </message>'
            b'<keep xml:space="preserve"> <a><b/></a> </keep></data>'
        )
        rendered = etree.fromstring(serialize_xml(root))
        for tag in ("message", "keep"):
            self.assertEqual(etree.tostring(root.find(tag), with_tail=False),
                             etree.tostring(rendered.find(tag), with_tail=False))

    def test_raw_adds_no_indentation(self):
        rendered = serialize_xml(etree.fromstring(SAMPLE.encode()), "raw")
        self.assertNotIn(b'\n  <', rendered)
        self.assertIn('乙太網路'.encode(), rendered)

    @patch("netconf_console.ncc._prepare_context")
    def test_offline_repairs_powershell_utf16_with_utf8_declaration(self, prepare):
        text = '<?xml version="1.0" encoding="UTF-8"?>\r\n' + SAMPLE
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "old file.xml"
            target = Path(directory) / "new directory" / "formatted.xml"
            source.write_bytes(text.encode("utf-16"))
            original = source.read_bytes()
            with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                code = ncc.main(["--format-xml", str(source), "--pretty", "--out", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Saved XML", stderr.getvalue())
            data = target.read_bytes()
            self.assertFalse(data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE)))
            self.assertIn(b"encoding='UTF-8'", data)
            self.assertNotIn(b"\x00", data)
            self.assertIn(b"\n  <interface>", data)
            self.assertEqual(etree.fromstring(data).find("{urn:test}interface/{urn:test}name").text, "乙太網路")
            self.assertEqual(source.read_bytes(), original)
        prepare.assert_not_called()

    def test_offline_honors_utf8_and_unicode_boms(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.xml"
            for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-32", "utf-16-be"):
                with self.subTest(encoding=encoding):
                    source.write_bytes(('<?xml version="1.0" encoding="UTF-8"?>' + SAMPLE).encode(encoding))
                    data = serialize_xml(read_xml(str(source)))
                    self.assertIn('乙太網路'.encode(), data)

    def test_offline_invalid_xml_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "broken.xml", Path(directory) / "saved.xml"
            source.write_bytes(b"<data><unclosed></data>")
            target.write_bytes(b"keep existing backup")
            with redirect_stderr(io.StringIO()):
                self.assertNotEqual(ncc.main(["--format-xml", str(source), "--out", str(target)]), 0)
            self.assertEqual(target.read_bytes(), b"keep existing backup")

    def test_offline_keeps_comments_and_never_expands_external_entities(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "private.txt"
            secret.write_text("must-not-be-expanded", encoding="utf-8")
            source = Path(directory) / "input.xml"
            source.write_text(
                '<!--before--><!DOCTYPE data [<!ENTITY external SYSTEM "' + secret.as_uri()
                + '">]><data>&external;</data><!--after-->', encoding="utf-8",
            )
            rendered = serialize_xml(read_xml(str(source)))
            self.assertNotIn(b"must-not-be-expanded", rendered)
            self.assertIn(b"&external;", rendered)
            self.assertIn(b"<!--before-->", rendered)
            self.assertIn(b"<!--after-->", rendered)

    def test_offline_stdin_and_stdout(self):
        stdin = SimpleNamespace(buffer=io.BytesIO(SAMPLE.encode()))
        with patch("sys.stdin", stdin), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(ncc.main(["--format-xml", "-", "--out", "-"]), 0)
        self.assertIn("\n  <interface>", stdout.getvalue())

    def test_query_saves_only_xml_and_rpc_error_preserves_existing_output(self):
        ctx = MagicMock()
        ctx.namespace_registry.mapping.return_value = {}
        operation = MagicMock()
        operation.invoke.return_value = etree.fromstring(SAMPLE.encode())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "running.xml"
            ns = SimpleNamespace(out=str(target), debug=False)
            with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                self.assertEqual(ncc._invoke_operation(ctx, operation, ns, [], "pretty"), 0)
            self.assertEqual(stdout.getvalue(), "")
            good = target.read_bytes()
            self.assertIn(b"\n  <interface>", good)
            operation.invoke.side_effect = RPCError(etree.fromstring(
                b'<rpc-error xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
                b'<error-type>application</error-type><error-tag>access-denied</error-tag>'
                b'<error-severity>error</error-severity></rpc-error>'
            ))
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertNotEqual(ncc._invoke_operation(ctx, operation, ns, [], "pretty"), 0)
            self.assertIn("access-denied", stdout.getvalue())
            self.assertEqual(target.read_bytes(), good)

    def test_interactive_per_command_output_and_out_do_not_leak(self):
        ctx = SimpleNamespace(output_mode="raw", namespace_registry=MagicMock())
        ctx.namespace_registry.mapping.return_value = {}
        parent = ncc.resolve_namespace(ncc.argparser().parse_args(["--interactive"]))
        expr = ncc.ExpressionOperation(ncc.expression_parser(ncc.SafeParser))
        with tempfile.TemporaryDirectory() as directory, patch.object(
            operations.GetConfig, "invoke", return_value=etree.fromstring(SAMPLE.encode())
        ):
            target = Path(directory) / "running file.xml"
            command = 'get-config --db running --pretty --out "%s"' % target
            with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                self.assertEqual(ncc._invoke_operation(ctx, expr, parent, [command], ctx.output_mode), 0)
            self.assertEqual(stdout.getvalue(), "")
            good = target.read_bytes()
            self.assertIn(b"\n  <interface>", good)
            with redirect_stdout(io.StringIO()) as stdout:
                ncc._invoke_operation(ctx, expr, parent, ["get-config --db running"], ctx.output_mode)
            self.assertIn("<data", stdout.getvalue())
            self.assertNotIn("\n  <interface>", stdout.getvalue())
            self.assertEqual(ctx.output_mode, "raw")
            self.assertEqual(target.read_bytes(), good)

    @patch("netconf_console.ncc._prepare_context")
    def test_rejects_ambiguous_file_output_before_connecting(self, prepare):
        for args in (
            ["--get", "--get-config", "--out", "result.xml"],
            ["--interactive", "--out", "result.xml"],
            ["--status", "--out", "result.xml"],
            ["--format-xml", "file.xml", "--call-home"],
            ["--format-xml", "file.xml", "--get"],
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                ncc.main(args)
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
