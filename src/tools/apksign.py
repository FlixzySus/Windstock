"""
APK Signature Scheme v2 -- sign and verify, from the standard library alone.

Why this exists: repackaging an APK (repackage_metadata.py) drops its
signature, and Android installs nothing unsigned. Signing normally means the
Android SDK's `apksigner` (Java) or uber-apk-signer; this module does the same
job with nothing installed, reusing the RSA + DER code of the server's
certificate generator (windstock/tools/certgen.py).

Scheme v2 (Android 7.0+): an "APK Signing Block" is inserted between the ZIP
entries and the central directory. It carries the SHA-256 of the three ZIP
sections (entries, central directory, end-of-central-directory), signed with
RSASSA-PKCS1-v1_5 (algorithm 0x0103), plus the signer's certificate. Layout as
in the Android source (ApkSigningBlockUtils / V2SchemeSigner):

    uint64 size | pairs | uint64 size | "APK Sig Block 42"
    pair  = uint64 len | uint32 id (0x7109871a) | v2 block
    v2    = lp(seq(lp(signer)))
    signer = lp(signed_data) | lp(seq(lp(uint32 alg | lp(sig)))) | lp(public key)
    signed_data = lp(seq(lp(uint32 alg | lp(digest)))) | lp(seq(lp(cert))) | lp(attrs)

    lp(x) = uint32 little-endian length of x, then x.

Only v2 is written. Android 5 and 6 read v1 (JAR) signatures only, so an APK
signed here installs on Android 7 or newer -- which is also the only Android
that needs the patched APK at all (see patch_apk.py).

    py apksign.py sign   in.apk out.apk [keys-dir]
    py apksign.py verify some.apk
"""
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
from windstock.tools import certgen  # noqa: E402

SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
ALG_RSA_PKCS1_SHA256 = 0x0103
CHUNK = 1 << 20
EOCD_SIG = b"PK\x05\x06"
DEFAULT_KEYS = os.path.join(HERE, "..", "server", "data", "apk_sign")


# ------------------------------------------------------------------ keys --
def load_or_make_keys(keys_dir=DEFAULT_KEYS):
    """The signing key + self-signed certificate, made on first use and kept,
    so a re-patched APK installs over the previous one (Android requires the
    same signer for an update). -> (key dict, cert DER)"""
    os.makedirs(keys_dir, exist_ok=True)
    kp, cp = os.path.join(keys_dir, "signing.key"), os.path.join(keys_dir, "signing.crt")
    if not (os.path.exists(kp) and os.path.exists(cp)):
        # Android reads nothing from the certificate but its public key, so a
        # long-lived self-signed one is all it needs. 20 years stays inside
        # UTCTime's 2049 limit.
        key_pem, cert_pem = certgen.make_ca(cn="Windstock APK signing", org="Windstock",
                                            days=365 * 20)
        fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key_pem)
        with open(cp, "w") as fh:
            fh.write(cert_pem)
        print(f"created a new APK signing key in {keys_dir} -- keep it: an APK signed "
              "with a different key cannot update one already installed")
    with open(kp) as fh:
        key = certgen.load_private_key(fh.read())
    with open(cp) as fh:
        cert_der = certgen._unpem(fh.read())
    return key, cert_der


# ------------------------------------------------------------ zip layout --
def _find_eocd(data):
    """Offset of the end-of-central-directory record (scanning back over a
    comment of up to 64 KiB, as every ZIP reader does)."""
    lo = max(0, len(data) - 22 - 65535)
    pos = data.rfind(EOCD_SIG, lo)
    if pos < 0:
        raise ValueError("not a ZIP file (no end-of-central-directory record)")
    return pos


def _sections(data):
    """Split an APK into (entries, existing signing block or b'', central
    directory, eocd). An existing signing block is found by its magic right
    before the central directory, so it can be replaced rather than stacked."""
    eocd_off = _find_eocd(data)
    eocd = data[eocd_off:]
    cd_size, cd_off = struct.unpack_from("<II", eocd, 12)
    cd = data[cd_off:cd_off + cd_size]
    block_start = cd_off
    if cd_off >= 32 and data[cd_off - 16:cd_off] == SIG_BLOCK_MAGIC:
        size, = struct.unpack_from("<Q", data, cd_off - 24)
        start = cd_off - size - 8
        if start >= 0:
            block_start = start
    return data[:block_start], data[block_start:cd_off], cd, eocd


def _eocd_with_cd_offset(eocd, cd_off):
    out = bytearray(eocd)
    struct.pack_into("<I", out, 16, cd_off)
    return bytes(out)


def _content_digest(entries, cd, eocd_for_digest):
    """The v2 'chunked SHA-256': every 1 MiB chunk of each section is hashed
    with a 0xa5 prefix and its length, then the chunk digests with 0x5a."""
    chunks = []
    for section in (entries, cd, eocd_for_digest):
        for i in range(0, len(section), CHUNK):
            c = section[i:i + CHUNK]
            chunks.append(hashlib.sha256(b"\xa5" + struct.pack("<I", len(c)) + c).digest())
    return hashlib.sha256(b"\x5a" + struct.pack("<I", len(chunks)) + b"".join(chunks)).digest()


def _lp(b):
    return struct.pack("<I", len(b)) + b


def _seq(items):
    return b"".join(_lp(i) for i in items)


# ------------------------------------------------------------------ sign --
def sign(src_apk, dst_apk, keys_dir=DEFAULT_KEYS):
    key, cert_der = load_or_make_keys(keys_dir)
    with open(src_apk, "rb") as fh:
        data = fh.read()
    entries, _old_block, cd, eocd = _sections(data)
    block_off = len(entries)
    digest = _content_digest(entries, cd, _eocd_with_cd_offset(eocd, block_off))

    spki = certgen._spki(key)
    signed_data = (_lp(_seq([struct.pack("<I", ALG_RSA_PKCS1_SHA256) + _lp(digest)]))
                   + _lp(_seq([cert_der]))
                   + _lp(b""))                                    # additional attributes
    signature = certgen._sign_sha256(key, signed_data)
    signer = (_lp(signed_data)
              + _lp(_seq([struct.pack("<I", ALG_RSA_PKCS1_SHA256) + _lp(signature)]))
              + _lp(spki))
    v2 = _lp(_seq([signer]))
    pair = struct.pack("<Q", 4 + len(v2)) + struct.pack("<I", V2_BLOCK_ID) + v2
    size = len(pair) + 8 + 16                  # everything after the first size field
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + SIG_BLOCK_MAGIC

    new_cd_off = block_off + len(block)
    out = entries + block + cd + _eocd_with_cd_offset(eocd, new_cd_off)
    with open(dst_apk, "wb") as fh:
        fh.write(out)
    return verify(dst_apk)


# ---------------------------------------------------------------- verify --
def _read_lp(buf, pos):
    n, = struct.unpack_from("<I", buf, pos)
    return buf[pos + 4:pos + 4 + n], pos + 4 + n


def _seq_items(buf):
    out, pos = [], 0
    while pos < len(buf):
        item, pos = _read_lp(buf, pos)
        out.append(item)
    return out


def _rsa_public(spki_der):
    """(n, e) out of a SubjectPublicKeyInfo."""
    _t, body, _e = certgen._read_tlv(spki_der)
    kids = certgen._children(body)
    bits = kids[1][1][1:]                                # drop the unused-bits byte
    _t, rsa, _e = certgen._read_tlv(bits)
    n, e = [int.from_bytes(b, "big") for _tag, b, _raw in certgen._children(rsa)]
    return n, e


def verify(apk):
    """Check the v2 signature the way Android does: find the block, recompute
    the content digest, verify the RSA signature over the signed data, and make
    sure the public key matches the certificate. Raises on any mismatch;
    returns the signer certificate (DER) when all is well."""
    with open(apk, "rb") as fh:
        data = fh.read()
    entries, block, cd, eocd = _sections(data)
    if not block or block[-16:] != SIG_BLOCK_MAGIC:
        raise ValueError("no APK Signing Block")
    size, = struct.unpack_from("<Q", block, 0)
    size2, = struct.unpack_from("<Q", block, len(block) - 24)
    if size != size2 or size + 8 != len(block):
        raise ValueError("signing block sizes disagree")
    pos, v2 = 8, None
    while pos < len(block) - 24:
        plen, = struct.unpack_from("<Q", block, pos)
        pid, = struct.unpack_from("<I", block, pos + 8)
        if pid == V2_BLOCK_ID:
            v2 = block[pos + 12:pos + 8 + plen]
        pos += 8 + plen
    if v2 is None:
        raise ValueError("no v2 signature in the signing block")
    signers_raw, _ = _read_lp(v2, 0)
    signers = _seq_items(signers_raw)
    if len(signers) != 1:
        raise ValueError(f"expected one signer, found {len(signers)}")
    s = signers[0]
    signed_data, p = _read_lp(s, 0)
    sigs_raw, p = _read_lp(s, p)
    spki, p = _read_lp(s, p)

    digests_raw, q = _read_lp(signed_data, 0)
    certs_raw, q = _read_lp(signed_data, q)
    cert = _seq_items(certs_raw)[0]
    alg, = struct.unpack_from("<I", _seq_items(digests_raw)[0], 0)
    claimed, _ = _read_lp(_seq_items(digests_raw)[0], 4)
    if alg != ALG_RSA_PKCS1_SHA256:
        raise ValueError(f"unexpected digest algorithm {alg:#x}")
    actual = _content_digest(entries, cd, _eocd_with_cd_offset(eocd, len(entries)))
    if actual != claimed:
        raise ValueError("content digest mismatch -- the APK was changed after signing")

    sig_item = _seq_items(sigs_raw)[0]
    salg, = struct.unpack_from("<I", sig_item, 0)
    sig, _ = _read_lp(sig_item, 4)
    if salg != ALG_RSA_PKCS1_SHA256:
        raise ValueError(f"unexpected signature algorithm {salg:#x}")
    n, e = _rsa_public(spki)
    k = (n.bit_length() + 7) // 8
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    t = certgen._SHA256_PREFIX + hashlib.sha256(signed_data).digest()
    if em != b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t:
        raise ValueError("signature does not verify")
    # The certificate's key must be the key that signed (Android checks this).
    _t, cbody, _e = certgen._read_tlv(cert)
    tbs = certgen._children(certgen._children(cbody)[0][1])
    if tbs[0][0] == 0xA0:
        tbs = tbs[1:]
    if tbs[5][2] != spki:
        raise ValueError("signer public key differs from the certificate's")
    return cert


def main(argv):
    if len(argv) >= 3 and argv[0] == "sign":
        cert = sign(argv[1], argv[2], argv[3] if len(argv) > 3 else DEFAULT_KEYS)
        print(f"signed {argv[2]} (v2, RSA-2048/SHA-256, cert sha256 "
              f"{hashlib.sha256(cert).hexdigest()[:16]}...), verified")
    elif len(argv) == 2 and argv[0] == "verify":
        cert = verify(argv[1])
        print(f"{argv[1]}: v2 signature OK, cert sha256 {hashlib.sha256(cert).hexdigest()}")
    else:
        print(__doc__.strip().splitlines()[-2].strip())
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
