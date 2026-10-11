"""
One command from your own Pokemon GO 0.29.0 or 0.35.0 APK to one that runs on
Android 7+ and talks to Windstock:

    py src/tools/patch_apk.py pokemon-go-0.29.0.apk
    -> pokemon-go-0.29.0-windstock.apk, next to the input

The build is read from the APK's manifest (--client 0.29|0.35 to say it
yourself). The one difference between them is step 2: 0.35's login code trusts
no certificate we can serve, so its login URLs become plain http:// and land on
the server's port-80 door (windstock/net/sso_bridge.py); 0.29 stays on https.

What it does, in order (each step is its own script in this folder):

  1. metadata_patch.py     Android 7+ refuses System.load("libNianticLabsPlugin.so")
                           without an absolute path; the il2cpp string literal is
                           repointed to /data/data/<package>/lib/... No Frida, no root.
  2. patch_sso_host.py     the five PTC login URLs move from sso.pokemon.com to
                           pgorelease.nianticlabs.com, the hostname that reliably
                           resolves to this server (a phone's second resolver or an
                           encrypted-DNS mode tends to win the race for pokemon.com,
                           which shows up as "unable to authenticate" with nothing
                           in the server log). Skip with --keep-sso-host.
  3. targetSdkVersion      Android lets an app trust USER-installed CA
                           certificates only while it targets API 23 or lower.
                           0.29 does; 0.35 targets 24+, so its game traffic
                           rejects the server's certificate ("certificate
                           unknown" in the server log) although Chrome accepts
                           it. The manifest's targetSdkVersion is set back to 23.
                           Skip with --keep-target-sdk.
  4. rebuild               the APK is rebuilt with the patched files, everything
                           else byte for byte; the old signature is dropped.
  5. apksign.py            signed with APK Signature Scheme v2 (Android 7+). The
                           key is made on first use and kept in src/server/data/
                           apk_sign/, so a later re-patch installs as an update.

Android 5 and 6 need none of this: install the original APK there. The game
targets API 23, so every Android version trusts the user-installed CA for it.

Only your own APK goes in; nothing patched is ever distributed.

    options: -o OUT.apk   --package com.example.pkg (if the APK was renamed)
             --keep-sso-host   --keys DIR
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
META_ENTRY = "assets/bin/Data/Managed/Metadata/global-metadata.dat"
MANIFEST_ENTRY = "AndroidManifest.xml"
TARGET_SDK_FOR_USER_CA = 23


# ---------------------------------------------------- binary AndroidManifest --
def _axml_strings(m):
    """The string pool of a binary XML (AXML) file, as a list; [] if it isn't one."""
    if len(m) < 8 or struct.unpack_from("<H", m, 0)[0] != 0x0003:
        return []
    pos = 8
    while pos + 8 <= len(m):
        ctype, hsize, csize = struct.unpack_from("<HHI", m, pos)
        if ctype == 0x0001:                                   # RES_STRING_POOL_TYPE
            n, _styles, flags, strings_start = struct.unpack_from("<IIII", m, pos + 8)
            offsets = struct.unpack_from(f"<{n}I", m, pos + hsize)   # right after the header
            base, out = pos + strings_start, []
            for off in offsets:
                q = base + off
                if flags & 0x100:                             # UTF-8 pool
                    def _len8(q):
                        v = m[q]; q += 1
                        if v & 0x80:
                            v = ((v & 0x7F) << 8) | m[q]; q += 1
                        return v, q
                    _chars, q = _len8(q)
                    nbytes, q = _len8(q)
                    out.append(m[q:q + nbytes].decode("utf-8", "replace"))
                else:                                         # UTF-16LE pool
                    ln, = struct.unpack_from("<H", m, q); q += 2
                    if ln & 0x8000:
                        lo, = struct.unpack_from("<H", m, q); q += 2
                        ln = ((ln & 0x7FFF) << 16) | lo
                    out.append(m[q:q + 2 * ln].decode("utf-16-le", "replace"))
            return out
        if csize == 0:
            break
        pos += csize
    return []


def _target_sdk(m):
    """-> (value, file offset of its data word) of android:targetSdkVersion in a
    binary manifest, or (None, None). An AXML attribute is 20 bytes: ns, name,
    rawValue, then a typed value {size=8, res0=0, type, data}; targetSdkVersion
    is an INT_DEC (type 0x10) with no raw string (rawValue 0xFFFFFFFF)."""
    strings = _axml_strings(m)
    if "targetSdkVersion" not in strings:
        return None, None
    idx = strings.index("targetSdkVersion")
    for pos in range(0, len(m) - 20, 4):
        _ns, name, raw, size, res0, typ, data = struct.unpack_from("<IIIHBBI", m, pos)
        if name == idx and raw == 0xFFFFFFFF and size == 8 and res0 == 0 and typ == 0x10:
            return data, pos + 16
    return None, None


def _repackage(src_apk, out_apk, replacements):
    """Rebuild the APK with some entries replaced (same logic as
    repackage_metadata.py, for any number of entries): every other entry is
    copied verbatim with its compression, and the old signature is dropped."""
    skip_sig = (".SF", ".RSA", ".DSA", ".EC")
    with zipfile.ZipFile(src_apk, "r") as zin, zipfile.ZipFile(out_apk, "w") as zout:
        for item in zin.infolist():
            name = item.filename
            up = name.upper()
            if up.startswith("META-INF/") and (up.endswith(skip_sig) or up.endswith("MANIFEST.MF")):
                continue
            data = replacements[name] if name in replacements else zin.read(name)
            zi = zipfile.ZipInfo(name, date_time=item.date_time)
            zi.compress_type = item.compress_type
            zi.external_attr = item.external_attr
            zi.internal_attr = item.internal_attr
            zi.create_system = item.create_system
            zout.writestr(zi, data)


def _detect_client(z):
    """'0.29' or '0.35' from the version name in the binary AndroidManifest.xml.
    Its string pool is UTF-16LE on these builds; UTF-8 is tried as well."""
    try:
        manifest = z.read("AndroidManifest.xml")
    except KeyError:
        return None
    for ver in ("0.35.0", "0.29.0"):
        if ver.encode("utf-16-le") in manifest or ver.encode() in manifest:
            return ver[:4]
    return None


PLUGIN_LITERAL = b"libNianticLabsPlugin.so"


def _literals(meta, needle):
    """String literals of an il2cpp global-metadata.dat that contain `needle`
    (same table the patch scripts repoint)."""
    try:
        sanity, _version = struct.unpack_from("<II", meta, 0)
        if sanity != 0xFAB11BAF:
            return []
        lit_off, lit_size, data_off, data_size = struct.unpack_from("<iiii", meta, 8)
    except struct.error:
        return []
    out = []
    for i in range(lit_size // 8):
        ln, di = struct.unpack_from("<Ii", meta, lit_off + i * 8)
        if 0 < ln < 300 and 0 <= di and di + ln <= data_size:
            lit = meta[data_off + di:data_off + di + ln]
            if needle in lit:
                out.append(lit)
    return out


def _run(script, *args):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *args],
                       capture_output=True, text=True)
    for line in (r.stdout + r.stderr).strip().splitlines():
        print("   " + line)
    if r.returncode != 0:
        raise SystemExit(f"!! {script} failed (see above)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("apk", help="your own, unmodified Pokemon GO 0.29.0 APK")
    ap.add_argument("-o", "--out", help="output APK (default: <input>-windstock.apk)")
    ap.add_argument("--package", default="com.nianticlabs.pokemongo",
                    help="the APK's package name, if it was renamed")
    ap.add_argument("--client", choices=("0.29", "0.35"),
                    help="which build this is, if it can't be read from the manifest")
    ap.add_argument("--keep-sso-host", action="store_true",
                    help="leave the PTC login URLs on sso.pokemon.com")
    ap.add_argument("--keep-target-sdk", action="store_true",
                    help="leave targetSdkVersion alone (the game then trusts no "
                         "user-installed CA on Android 7+ if it targets 24+)")
    ap.add_argument("--keys", default=None, help="folder for the signing key")
    a = ap.parse_args()

    src = os.path.abspath(a.apk)
    out = os.path.abspath(a.out) if a.out else os.path.splitext(src)[0] + "-windstock.apk"
    if not os.path.isfile(src):
        raise SystemExit(f"!! not found: {src}")
    try:
        with zipfile.ZipFile(src) as z:
            meta = z.read(META_ENTRY)
            manifest = z.read(MANIFEST_ENTRY)
            client = a.client or _detect_client(z)
    except (zipfile.BadZipFile, KeyError):
        raise SystemExit(f"!! {os.path.basename(src)} is not a Pokemon GO APK "
                         f"(no {META_ENTRY})")
    new_path = f"/data/data/{a.package}/lib/libNianticLabsPlugin.so".encode()
    if new_path in meta:
        raise SystemExit("!! this APK is already patched -- start from the original one")
    if client is None:
        raise SystemExit("!! can't tell whether this is 0.29.0 or 0.35.0 from the manifest "
                         "-- say which with --client 0.29 or --client 0.35")
    print(f"Pokemon GO {client} APK: {os.path.basename(src)}")

    with tempfile.TemporaryDirectory() as tmp:
        m0 = os.path.join(tmp, "meta0.dat")
        m1 = os.path.join(tmp, "meta1.dat")
        m2 = os.path.join(tmp, "meta2.dat")
        unsigned = os.path.join(tmp, "unsigned.apk")
        with open(m0, "wb") as fh:
            fh.write(meta)

        if _literals(meta, PLUGIN_LITERAL):
            print("1. plugin path for Android 7+ (metadata_patch.py)")
            _run("metadata_patch.py", m0, m1, a.package)
        else:
            # 0.35 has no such literal: it does not load the plugin by that
            # relative name, so the Android 7+ System.load rule never bites and
            # there is nothing to repoint. Say what it has instead, so a launch
            # crash on that build can be traced to the right string.
            m1 = m0
            print(f"1. plugin path: this build has no {PLUGIN_LITERAL.decode()!r} literal, "
                  "so the Android 7+ plugin-path patch is not needed -- skipped")
            related = _literals(meta, b"NianticLabsPlugin") + [
                l for l in _literals(meta, b".so") if b"NianticLabsPlugin" not in l]
            for lit in related[:8]:
                print(f"   (plugin-related string: {lit.decode(errors='replace')!r})")
        if a.keep_sso_host:
            m2 = m1
            print("2. PTC login URLs left as they are (--keep-sso-host)")
            if client == "0.35":
                print("   !! 0.35 cannot log in over https to this server; without this "
                      "patch the login will fail (see sso_bridge.py)")
        elif client == "0.35":
            print("2. PTC login URLs -> http://pgorelease.nianticlabs.com, the plain-HTTP "
                  "door on port 80 (patch_sso_host.py)")
            _run("patch_sso_host.py", m1, m2, "pgorelease.nianticlabs.com", "http")
        else:
            print("2. PTC login URLs -> pgorelease.nianticlabs.com (patch_sso_host.py)")
            _run("patch_sso_host.py", m1, m2)
        replacements = {}
        with open(m2, "rb") as fh:
            replacements[META_ENTRY] = fh.read()
        sdk, off = _target_sdk(manifest)
        if sdk is None:
            print("3. targetSdkVersion: not found in the manifest -- left as is")
        elif a.keep_target_sdk:
            print(f"3. targetSdkVersion {sdk} left as is (--keep-target-sdk)")
        elif sdk <= TARGET_SDK_FOR_USER_CA:
            print(f"3. targetSdkVersion {sdk}: user-installed CA certificates are "
                  "already trusted -- nothing to do")
        else:
            fixed = bytearray(manifest)
            struct.pack_into("<I", fixed, off, TARGET_SDK_FOR_USER_CA)
            replacements[MANIFEST_ENTRY] = bytes(fixed)
            print(f"3. targetSdkVersion {sdk} -> {TARGET_SDK_FOR_USER_CA}, so the game "
                  "trusts the user-installed CA certificate (Android ignores user "
                  "CAs for apps targeting 24+)")
        print("4. rebuilding the APK")
        _repackage(src, unsigned, replacements)
        print("5. signing (apksign.py, scheme v2)")
        sys.path.insert(0, HERE)
        import apksign
        apksign.sign(unsigned, out, a.keys or apksign.DEFAULT_KEYS)

    size_mb = os.path.getsize(out) / 1e6
    print(f"\nwrote {out} ({size_mb:.1f} MB), signature verified")
    print("Install it on Android 7 or newer (uninstall the official Pokemon GO first:")
    print("the signer differs, so Android refuses it as an update). Then follow")
    print("src/server/ANDROID_SETUP.md from step 3.")


if __name__ == "__main__":
    main()
