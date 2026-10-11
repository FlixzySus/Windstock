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
  3. repackage_metadata.py the APK is rebuilt with the patched file, everything
                           else byte for byte; the old signature is dropped.
  4. apksign.py            signed with APK Signature Scheme v2 (Android 7+). The
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
    ap.add_argument("--keys", default=None, help="folder for the signing key")
    a = ap.parse_args()

    src = os.path.abspath(a.apk)
    out = os.path.abspath(a.out) if a.out else os.path.splitext(src)[0] + "-windstock.apk"
    if not os.path.isfile(src):
        raise SystemExit(f"!! not found: {src}")
    try:
        with zipfile.ZipFile(src) as z:
            meta = z.read(META_ENTRY)
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
        print("3. rebuilding the APK (repackage_metadata.py)")
        _run("repackage_metadata.py", src, m2, unsigned)
        print("4. signing (apksign.py, scheme v2)")
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
