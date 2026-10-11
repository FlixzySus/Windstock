"""
One command from your own Pokemon GO 0.29.0 APK to one that runs on Android 7+
and talks to Windstock:

    py src/tools/patch_apk.py pokemon-go-0.29.0.apk
    -> pokemon-go-0.29.0-windstock.apk, next to the input

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
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
META_ENTRY = "assets/bin/Data/Managed/Metadata/global-metadata.dat"


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
    except (zipfile.BadZipFile, KeyError):
        raise SystemExit(f"!! {os.path.basename(src)} is not a Pokemon GO APK "
                         f"(no {META_ENTRY})")
    new_path = f"/data/data/{a.package}/lib/libNianticLabsPlugin.so".encode()
    if new_path in meta:
        raise SystemExit("!! this APK is already patched -- start from the original one")

    with tempfile.TemporaryDirectory() as tmp:
        m0 = os.path.join(tmp, "meta0.dat")
        m1 = os.path.join(tmp, "meta1.dat")
        m2 = os.path.join(tmp, "meta2.dat")
        unsigned = os.path.join(tmp, "unsigned.apk")
        with open(m0, "wb") as fh:
            fh.write(meta)

        print("1. plugin path for Android 7+ (metadata_patch.py)")
        _run("metadata_patch.py", m0, m1, a.package)
        if a.keep_sso_host:
            m2 = m1
            print("2. PTC login URLs left as they are (--keep-sso-host)")
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
