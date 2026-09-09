"""Explicit offline demo. Never creates sockets or uses saved credentials."""

from __future__ import annotations

import threading
from copy import deepcopy

from lxml import etree

from .client import ApplyResult, ReadOptions, Snapshot
from .model import NC, WD, children, identity, EditError
from .schema import SchemaIndex

SOURCES = {
    "demo-interfaces": '''module demo-interfaces {
      yang-version 1.1; namespace "urn:ietf:params:xml:ns:yang:ietf-interfaces"; prefix if;
      typedef switch-state { type boolean; default true; }
      container interfaces {
        list interface {
          key "name";
          leaf name { type string; }
          leaf description { type string; }
          leaf enabled { type switch-state; }
          leaf oper-status { config false; type enumeration { enum up; enum down; } }
          container statistics { config false; leaf in-octets { type uint64; } }
        }
      }
    }''',
    "demo-radio": '''module demo-radio {
      yang-version 1.1; namespace "urn:o-ran:interfaces:1.0"; prefix oran;
      import demo-interfaces { prefix if; }
      augment "/if:interfaces/if:interface" {
        leaf l2-mtu { type uint16; default 1500; }
        leaf vlan-tagging { type boolean; default true; }
        leaf mac-address { config false; type string; }
        container port-reference { leaf port-name { type string; } leaf port-number { type uint16; } }
      }
    }''',
    "demo-hardware": '''module demo-hardware {
      yang-version 1.1; namespace "urn:ietf:params:xml:ns:yang:ietf-hardware"; prefix hw;
      container hardware {
        list component { key "name";
          leaf name { type string; }
          leaf alias { type string; }
          leaf serial-num { config false; type string; }
          leaf temperature { config false; type int32; }
        }
      }
      container demo-note {presence "Create an offline demo note";
        leaf text {type string; mandatory true;}
      }
    }''',
}

DEMO_XML = '''<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" xmlns:wd="urn:ietf:params:xml:ns:netconf:default:1.0">
  <interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">
    <interface>
      <name>eth0</name>
      <description>O-RU management / 管理介面</description>
      <enabled wd:default="true">true</enabled>
      <l2-mtu xmlns="urn:o-ran:interfaces:1.0" wd:default="true">1500</l2-mtu>
      <vlan-tagging xmlns="urn:o-ran:interfaces:1.0">true</vlan-tagging>
      <mac-address xmlns="urn:o-ran:interfaces:1.0">a2:06:8b:a6:72:1d</mac-address>
      <port-reference xmlns="urn:o-ran:interfaces:1.0"><port-name>ru-port0</port-name><port-number>0</port-number></port-reference>
      <oper-status>up</oper-status>
      <statistics><in-octets>3821056</in-octets></statistics>
    </interface>
    <interface><name>eth1</name><description>Fronthaul</description><enabled>true</enabled><oper-status>down</oper-status></interface>
  </interfaces>
  <hardware xmlns="urn:ietf:params:xml:ns:yang:ietf-hardware">
    <component><name>ru-port0</name><alias>Radio unit</alias><serial-num>DEMO-0001</serial-num><temperature>42</temperature></component>
  </hardware>
</data>'''


class DemoClient:
    context = None
    capabilities = ["urn:ietf:params:netconf:capability:writable-running:1.0",
                    "urn:ietf:params:netconf:capability:candidate:1.0",
                    "urn:ietf:params:netconf:capability:startup:1.0"]

    def __init__(self):
        self.schema = SchemaIndex.compile(SOURCES)
        self.data = etree.fromstring(DEMO_XML.encode())
        self.cancel = threading.Event()
        self.connected = True
        self.sent = []

    def read(self, options=ReadOptions(), root_tag=None):
        data = deepcopy(self.data)
        def prune(parent, path):
            for child in children(parent):
                info = self.schema.lookup(path + (child.tag,))
                if (not options.state and info and info.config is False
                        or not options.defaults and child.get("{%s}default" % WD) == "true"):
                    parent.remove(child)
                else:
                    prune(child, path + (child.tag,))
        prune(data, ())
        if root_tag:
            for child in children(data):
                if child.tag != root_tag:
                    data.remove(child)
        return Snapshot(data, options, "report-all-tagged" if options.defaults else None)

    def disconnect(self):
        self.connected = False

    def load_schema(self, _directory="", _force=False, progress=lambda _x: None):
        return self.schema

    def apply(self, _selection, plan, options):
        self.sent.append(plan.wire_xml)
        config = plan.rpc.find("{%s}edit-config/{%s}config" % (NC, NC))
        def merge(parent, node, path):
            info = self.schema.lookup(path)
            key = identity(node, self.schema, path)
            found = next((child for child in children(parent) if child.tag == node.tag
                          and identity(child, self.schema, path) == key), None)
            if node.get("{%s}operation" % NC) == "create" and found is not None:
                raise EditError("data-exists: the new instance already exists")
            if node.get("{%s}operation" % NC) == "remove":
                if found is not None:
                    parent.remove(found)
            elif info and info.kind in {"leaf", "leaf-list"} or found is None:
                replacement = deepcopy(node)
                for descendant in replacement.iter():
                    descendant.attrib.pop("{%s}operation" % NC, None)
                if found is not None:
                    parent.replace(found, replacement)
                else:
                    parent.append(replacement)
            else:
                for child in children(node):
                    merge(found, child, path + (child.tag,))
        for node in children(config):
            merge(self.data, node, (node.tag,))
        return ApplyResult('<rpc-reply xmlns="%s" message-id="%s"><ok/></rpc-reply>' % (NC, plan.rpc.get("message-id")), self.read(options))
