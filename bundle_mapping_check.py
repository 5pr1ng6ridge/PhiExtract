# -*- coding: utf-8 -*-
"""
Diagnostic: verify how Addressables catalog entries map to .bundle files in an APK.

Usage:  python bundle_mapping_check.py <apk> [song-prefix]

Root cause it demonstrates:
  * legacy mapping (bundle KEY name from m_KeyDataString/Bucket tables, e.g.
    "<h1>_<h2>.bundle") does NOT exist in current APKs -> 0 hits -> silent empty export
  * correct mapping (entry.dependencyKey -> bucket entry -> entry.internalId ->
    m_InternalIds[idx] basename, e.g. "<hash>.bundle") -> 100% hits
"""
import base64
import json
import sys
import zipfile


def parse_catalog(cat):
    key = base64.b64decode(cat["m_KeyDataString"])
    bucket = base64.b64decode(cat["m_BucketDataString"])
    entry = base64.b64decode(cat["m_EntryDataString"])
    ids = cat["m_InternalIds"]

    class R:
        def __init__(self, b):
            self.b, self.p = b, 0

        def read_i32(self):
            v = self.b[self.p] | self.b[self.p + 1] << 8 | self.b[self.p + 2] << 16 | self.b[self.p + 3] << 24
            self.p += 4
            return v

    r = R(bucket)
    key_table = []  # key_table[i] = [key_string, [entry_index, ...]]
    for _ in range(r.read_i32()):
        kp = r.read_i32()
        kt = key[kp]
        kp += 1
        if kt == 0:      # AsciiString: [type][int32 len][bytes]
            ln = key[kp]; kp += 4
            kv = key[kp:kp + ln].decode("utf-8", "replace")
        elif kt == 1:    # UnicodeString
            ln = key[kp]; kp += 4
            kv = key[kp:kp + ln].decode("utf-16")
        else:            # small int key
            kv = key[kp]
        key_table.append([kv, [r.read_i32() for _ in range(r.read_i32())]])

    def record(i):  # 28-byte Entry: internalId, providerIndex, dependencyKey, depHash, dataIndex, primaryKey, resourceType
        b = entry[4 + 28 * i:4 + 28 * i + 28]
        return [int.from_bytes(b[j:j + 4], "little") for j in range(0, 28, 4)]

    n_entries = (len(entry) - 4) // 28
    return key_table, record, n_entries, ids


def main():
    apk_path = sys.argv[1]
    prefix = sys.argv[2] if len(sys.argv) > 2 else "Assets/Tracks/"
    apk_names = {n.split("/")[-1] for n in zipfile.ZipFile(apk_path).namelist()
                 if n.startswith("assets/aa/Android/")}
    cat = json.loads(zipfile.ZipFile(apk_path).read("assets/aa/catalog.json").decode("utf-8"))
    key_table, record, n_entries, ids = parse_catalog(cat)

    def legacy_name(e):
        return key_table[record(e)[2]][0]

    def fixed_name(e):
        dep = record(e)[2]
        entries = key_table[dep][1] if 0 <= dep < len(key_table) else []
        if not entries:
            return None
        idx = record(entries[0])[0]
        return ids[idx].split("/")[-1] if idx < len(ids) else None

    legacy_hits = fixed_hits = total = 0
    for i in range(n_entries):
        if record(i)[1] != 2:      # providerIndex 2 == BundledAssetProvider
            continue
        total += 1
        legacy_hits += legacy_name(i) in apk_names
        fixed_hits += fixed_name(i) in apk_names
    print(f"APK bundles                         : {len(apk_names)}")
    print(f"bundled-asset entries               : {total}")
    print(f"legacy key-name lookup hits         : {legacy_hits}")
    print(f"depKey->internalId lookup hits      : {fixed_hits}")

    for i, (k, entries) in enumerate(key_table):
        if isinstance(k, str) and k.startswith(prefix) and entries:
            print(f"\nsample: {k}")
            for e in entries[:6]:
                print("   legacy=%-62s %s" % (legacy_name(e), "IN_APK" if legacy_name(e) in apk_names else "MISSING"))
                print("   fixed =%-62s %s" % (fixed_name(e), "IN_APK" if fixed_name(e) in apk_names else "MISSING"))
            break


if __name__ == "__main__":
    main()
