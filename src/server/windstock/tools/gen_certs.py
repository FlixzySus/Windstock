"""
Generate a root CA + a single leaf cert covering every hostname the 0.29
client talks to. Install certs/ca.crt on the device/emulator (as a trusted
CA) and the client will accept our TLS for these hosts.

Run:  py gen_certs.py

Uses the ``openssl`` tool when it is installed. Without one (an Android phone
running the server in the Windstock app) the same certificates are made by
``certgen.py``, which needs nothing but the Python standard library.

The CA is PRIVATE TO THIS INSTALL. It is generated here, never shipped, never
committed (``*.key`` is in .gitignore): whoever holds ``ca.key`` can read the
traffic of every phone that trusts ``ca.crt``. Share ``ca.crt`` only.
"""
import os
import shutil
import subprocess
import sys
import tempfile

# Allow running this file directly (``py gen_certs.py``) as well as via -m.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from windstock.config import paths  # noqa: E402

# Same override the server itself honours (net/server.py).
CERTS = os.environ.get("CERT_DIR", paths.CERT_DIR)

# Apple's cap is 398 days; stay just under it.
LEAF_DAYS = "397"

CA_SUBJECT = ("US", "PoGO Private Server", "PoGO Private CA")
LEAF_CN = "pgorelease.nianticlabs.com"

SANS = [
    # The in-game Settings "support" button opens pokemongo.zendesk.com/hc in the
    # phone's browser. We redirect that host and serve the Help Center there, so
    # the cert has to cover it -- signed by the same CA the phone already trusts.
    "pokemongo.zendesk.com",
    "*.zendesk.com",
    "sso.pokemon.com",
    "*.pokemon.com",
    "pokemon.com",
    "pgorelease.nianticlabs.com",
    "holo.nianticlabs.com",
    "www.nianticlabs.com",
    "*.nianticlabs.com",
    "nianticlabs.com",
]


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def _path(name):
    return os.path.join(CERTS, name)


def have_certs():
    return os.path.exists(_path("server.crt")) and os.path.exists(_path("server.key"))


def _have_ca():
    return os.path.exists(_path("ca.crt")) and os.path.exists(_path("ca.key"))


def _write_private(path, text):
    """Owner-only, from the first byte: no window where the key is world-readable."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _main_openssl():
    p = _path
    c, o, cn = CA_SUBJECT

    # 1) Root CA (with explicit CA basicConstraints + keyUsage; strict
    #    verifiers and Android both want these present).
    #    REUSED if it already exists. Regenerating it would invalidate the CA
    #    every phone has already installed, breaking every client at once --
    #    which makes adding a hostname to SANS a trap. Delete certs/ca.* by hand
    #    if you really want a fresh root.
    if _have_ca():
        print("keeping the existing CA (phones already trust it)")
    else:
        run("openssl", "genrsa", "-out", p("ca.key"), "2048")
        os.chmod(p("ca.key"), 0o600)
        run("openssl", "req", "-x509", "-new", "-nodes", "-key", p("ca.key"),
            "-sha256", "-days", "3650", "-out", p("ca.crt"),
            "-subj", f"/C={c}/O={o}/CN={cn}",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        print("created a NEW root CA -- every phone must reinstall ca.crt")

    # 2) Leaf key + CSR
    run("openssl", "genrsa", "-out", p("server.key"), "2048")
    os.chmod(p("server.key"), 0o600)
    run("openssl", "req", "-new", "-key", p("server.key"), "-out", p("server.csr"),
        "-subj", f"/C={c}/O={o}/CN={LEAF_CN}")

    # 3) Sign leaf with SAN extensions.
    #    LEAF_DAYS is 397, not 3650, on purpose: Apple rejects TLS server certs
    #    whose validity exceeds 398 days, so an iPhone refuses the handshake no
    #    matter how thoroughly the root is trusted. Android doesn't enforce
    #    this, which is why a long-lived leaf worked there. Re-run this script
    #    once a year; the CA above is untouched, so no phone has to reinstall.
    ext = ("subjectAltName=" + ",".join(f"DNS:{h}" for h in SANS) + "\n"
           "extendedKeyUsage=serverAuth\n"
           "keyUsage=critical,digitalSignature,keyEncipherment\n"
           "basicConstraints=CA:FALSE\n")
    with tempfile.NamedTemporaryFile("w", suffix=".ext", delete=False) as f:
        f.write(ext)
        extfile = f.name
    try:
        run("openssl", "x509", "-req", "-in", p("server.csr"),
            "-CA", p("ca.crt"), "-CAkey", p("ca.key"), "-CAcreateserial",
            "-out", p("server.crt"), "-days", LEAF_DAYS, "-sha256",
            "-extfile", extfile)
    finally:
        os.unlink(extfile)


def _main_python():
    """The same two certificates, with no openssl binary (see certgen.py)."""
    from windstock.tools import certgen
    p = _path
    c, o, cn = CA_SUBJECT
    if _have_ca():
        print("keeping the existing CA (phones already trust it)")
    else:
        print("no openssl here -- generating the keys in pure Python (takes a moment)")
        key_pem, cert_pem = certgen.make_ca(cn=cn, org=o, country=c)
        _write_private(p("ca.key"), key_pem)
        with open(p("ca.crt"), "w") as fh:
            fh.write(cert_pem)
        print("created a NEW root CA -- every phone must reinstall ca.crt")
    with open(p("ca.key")) as fh:
        ca_key_pem = fh.read()
    with open(p("ca.crt")) as fh:
        ca_cert_pem = fh.read()
    key_pem, cert_pem = certgen.make_leaf(ca_key_pem, ca_cert_pem, LEAF_CN, SANS,
                                          org=o, country=c, days=int(LEAF_DAYS))
    _write_private(p("server.key"), key_pem)
    with open(p("server.crt"), "w") as fh:
        fh.write(cert_pem)


def main():
    os.makedirs(CERTS, exist_ok=True)
    if shutil.which("openssl"):
        _main_openssl()
    else:
        _main_python()
    print("Wrote:")
    for n in ("ca.crt", "ca.key", "server.crt", "server.key"):
        print("  certs/" + n)
    print("\nInstall certs/ca.crt on your device/emulator as a trusted CA.")
    print("Keep ca.key and server.key to yourself.")


def ensure():
    """Make the certificates if they are missing. True when they were made."""
    if have_certs():
        return False
    main()
    return True


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print("openssl failed:", e, file=sys.stderr)
        sys.exit(1)
