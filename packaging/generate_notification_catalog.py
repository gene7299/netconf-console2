"""Regenerate the offline notification choices from the supplied O-RAN YANG set.

uv run --python 3.13 -- python packaging/generate_notification_catalog.py
Only structural/type metadata is bundled. Missing external typedefs remain
labelled with their declared type, without invented enumerations.
"""
import json
from pathlib import Path
from netconf_console.gui.schema import SchemaIndex
from netconf_console.gui.subscription_templates import notification_catalog, _type_details

ROOT = Path(__file__).resolve().parents[1]
sources = {p.stem: p.read_text(encoding="utf-8-sig") for p in
           (ROOT / "spec/O-RAN.WG4.TS.MP-YANGs-R004-v17.01").rglob("*.yang")}
schema = SchemaIndex.compile(sources)
catalog = notification_catalog(schema.modules)
pm = schema.modules["o-ran-performance-management"]
config = next(n for n in pm.i_children if n.arg == "performance-measurement-objects")
mapping = {
    "transceiver": "transceiver-stats", "rx-window": "rx-window-stats",
    "tx": "tx-stats", "shared-cell": "shared-cell-stats", "epe": "epe-statistics",
    "symbol-rssi": "symbol-rssi-stats", "tssi": "tssi-stats", "rssi": "rssi-stats",
    "tx-antenna": "tx-antenna-stats", "tx-output-power": "tx-output-power-stats",
    "ethernet": "ethernet-stats",
}
groups = {}
for prefix, group in mapping.items():
    node = next(n for n in config.i_children if n.arg == prefix + "-measurement-objects")
    leaf = next(n for n in node.i_children if n.arg == "measurement-object")
    groups[group] = _type_details(leaf)[2]
    # Annex B counts TX_POWER once; TX_POPWER is the deprecated misspelling.
    groups[group] = [value for value in groups[group] if value != "TX_POPWER"]
data = {"source": "O-RAN.WG4.TS.MP-YANGs-R004-v17.01", "notifications": catalog,
        "measurement_groups": groups,
        "common_faults": json.loads((ROOT / "packaging/oran_mp17_common_faults.json").read_text(encoding="utf-8")),
        "module_notices": {name: schema.modules[name].search_one("description").arg
                           for name in sorted({event["module"] for event in catalog.values()})}}
dest = ROOT / "ncc/netconf_console/gui/assets/notification_catalog.json"
dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Generated", len(catalog), "notifications;", len(groups), "PM groups;",
      sum(map(len, groups.values())), "PM objects")
