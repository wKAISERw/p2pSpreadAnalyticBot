#!/usr/bin/env python3
"""
Витягує всі deeplink intent-filter'и з APK (AndroidManifest.xml).

Usage:
    python extract_deeplinks.py <path-to.apk> [--filter p2p,order,trade,advertiser]

Виводить таблицю: activity | scheme | host | path-rule
Працює з base.apk. Для .xapk/.apks — спочатку розпакувати і взяти base.apk.
"""
import sys
import argparse
from collections import defaultdict
from xml.dom import minidom

try:
    from pyaxmlparser.axmlprinter import AXMLPrinter
except ImportError:
    sys.exit("pip install pyaxmlparser --break-system-packages")

import zipfile

ANDROID_NS = "http://schemas.android.com/apk/res/android"

PATH_ATTRS = [
    ("path", "="),
    ("pathPrefix", "prefix"),
    ("pathPattern", "pattern"),
    ("pathAdvancedPattern", "adv"),
    ("pathSuffix", "suffix"),
]


def attr(node, name):
    return node.getAttributeNS(ANDROID_NS, name) or None


def load_manifest(apk_path):
    with zipfile.ZipFile(apk_path) as z:
        raw = z.read("AndroidManifest.xml")
    return minidom.parseString(AXMLPrinter(raw).get_xml())


def extract(dom):
    rows = []
    for tag in ("activity", "activity-alias", "service", "receiver"):
        for comp in dom.getElementsByTagName(tag):
            comp_name = attr(comp, "name") or "?"
            exported = attr(comp, "exported")
            for f in comp.getElementsByTagName("intent-filter"):
                actions = [attr(a, "name") for a in f.getElementsByTagName("action")]
                if "android.intent.action.VIEW" not in actions:
                    continue
                cats = [attr(c, "name") for c in f.getElementsByTagName("category")]
                if "android.intent.category.BROWSABLE" not in cats:
                    continue
                autoverify = attr(f, "autoVerify")
                datas = f.getElementsByTagName("data")
                schemes = [attr(d, "scheme") for d in datas if attr(d, "scheme")]
                hosts = [attr(d, "host") for d in datas if attr(d, "host")]
                paths = []
                for d in datas:
                    for a, kind in PATH_ATTRS:
                        v = attr(d, a)
                        if v:
                            paths.append(f"{kind}:{v}")
                rows.append({
                    "component": comp_name,
                    "exported": exported,
                    "autoVerify": autoverify,
                    "schemes": sorted(set(schemes)),
                    "hosts": sorted(set(hosts)),
                    "paths": paths or ["<any>"],
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("--filter", default="",
                    help="кома-розділені підрядки; показати лише рядки, що містять хоч один")
    ap.add_argument("--schemes-only", action="store_true",
                    help="лише зведений список custom-схем і хостів")
    args = ap.parse_args()

    dom = load_manifest(args.apk)
    pkg = dom.documentElement.getAttribute("package")
    rows = extract(dom)

    needles = [s.strip().lower() for s in args.filter.split(",") if s.strip()]

    print(f"# package: {pkg}")
    print(f"# browsable VIEW intent-filters: {len(rows)}\n")

    all_schemes = defaultdict(set)
    for r in rows:
        for s in r["schemes"]:
            for h in (r["hosts"] or ["<no-host>"]):
                all_schemes[s].add(h)

    print("## Схеми та хости")
    for s in sorted(all_schemes):
        hs = sorted(all_schemes[s])
        print(f"  {s}://  ->  {', '.join(hs[:12])}{' …' if len(hs) > 12 else ''}")
    print()

    if args.schemes_only:
        return

    print("## Intent-filters")
    for r in rows:
        blob = (r["component"] + " " + " ".join(r["schemes"]) +
                " " + " ".join(r["hosts"]) + " " + " ".join(r["paths"])).lower()
        if needles and not any(n in blob for n in needles):
            continue
        av = " autoVerify" if r["autoVerify"] == "true" else ""
        print(f"\n[{r['component']}]{av}")
        print(f"  scheme : {', '.join(r['schemes']) or '-'}")
        print(f"  host   : {', '.join(r['hosts']) or '-'}")
        for p in r["paths"]:
            print(f"  path   : {p}")


if __name__ == "__main__":
    main()
