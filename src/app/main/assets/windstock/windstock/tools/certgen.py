"""
Certificate generator with no openssl binary and no third-party package:
an RSA root CA and a leaf certificate, from the standard library alone.

gen_certs.py prefers the ``openssl`` tool when it is installed and falls back
to this module when it is not -- which is the case on an Android phone running
the server inside the Windstock app. The certificates come out the same shape
either way: a 2048-bit RSA CA with CA:TRUE + keyCertSign, and a leaf with the
subjectAltName list, serverAuth and digitalSignature/keyEncipherment, signed
with SHA-256. Apple's TLS rules (RSA >= 2048, SHA-2, a SAN, EKU, a short
validity) are all met, so the output works for an iPhone too.

Only what X.509 needs is implemented: a DER encoder for a handful of tags, a
DER reader just good enough to load a CA made earlier (by openssl or by us),
Miller-Rabin prime generation, and PKCS#1 v1.5 signing.
"""
import base64
import datetime
import hashlib
import math
import secrets

OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_CN, OID_C, OID_O = "2.5.4.3", "2.5.4.6", "2.5.4.10"
OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_SUBJECT_ALT_NAME = "2.5.29.17"
OID_EXT_KEY_USAGE = "2.5.29.37"
OID_SUBJECT_KEY_ID = "2.5.29.14"
OID_AUTHORITY_KEY_ID = "2.5.29.35"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"

# DigestInfo prefix for SHA-256 (PKCS#1 v1.5, RFC 8017 section 9.2 note 1).
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


# ----------------------------------------------------------------- DER out --
def _length(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, body):
    return bytes([tag]) + _length(len(body)) + body


def _integer(v):
    # +8 bits, not +7: a leading zero byte is kept whenever the top bit is set,
    # which is how DER says a positive INTEGER is written.
    return _tlv(0x02, v.to_bytes((v.bit_length() + 8) // 8, "big"))


def _sequence(*parts):
    return _tlv(0x30, b"".join(parts))


def _set(*parts):
    return _tlv(0x31, b"".join(parts))


def _oid(dotted):
    arcs = [int(a) for a in dotted.split(".")]
    body = bytes([40 * arcs[0] + arcs[1]])
    for v in arcs[2:]:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append(0x80 | (v & 0x7F))
            v >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def _bit_string(data, unused=0):
    return _tlv(0x03, bytes([unused]) + data)


def _octet_string(data):
    return _tlv(0x04, data)


def _boolean(v):
    return _tlv(0x01, b"\xff" if v else b"\x00")


_NULL = b"\x05\x00"


def _utf8(s):
    return _tlv(0x0C, s.encode("utf-8"))


def _printable(s):
    return _tlv(0x13, s.encode("ascii"))


def _utc_time(dt):
    return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def _explicit(n, body):
    return _tlv(0xA0 | n, body)


def _pem(label, der):
    b64 = base64.b64encode(der).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return (f"-----BEGIN {label}-----\n" + "\n".join(lines)
            + f"\n-----END {label}-----\n")


# ------------------------------------------------------------------ DER in --
def _unpem(text):
    lines = [ln.strip() for ln in text.splitlines()]
    body = "".join(ln for ln in lines if ln and not ln.startswith("-----"))
    return base64.b64decode(body)


def _read_tlv(data, pos=0):
    """-> (tag, content, end). Definite lengths only, which is all DER has."""
    tag = data[pos]
    n = data[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    return tag, data[pos:pos + n], pos + n


def _children(content):
    out, pos = [], 0
    while pos < len(content):
        tag, body, end = _read_tlv(content, pos)
        out.append((tag, body, content[pos:end]))
        pos = end
    return out


def load_private_key(pem_text):
    """An RSA private key as written by openssl or by us: PKCS#8 ('PRIVATE
    KEY') or PKCS#1 ('RSA PRIVATE KEY'). -> dict of the RSA parameters."""
    _tag, seq, _end = _read_tlv(_unpem(pem_text))
    kids = _children(seq)
    if len(kids) >= 3 and kids[1][0] == 0x30:            # PKCS#8 wrapper
        _tag, seq, _end = _read_tlv(kids[2][1])            # the OCTET STRING
        kids = _children(seq)
    ints = [int.from_bytes(body, "big") for tag, body, _raw in kids if tag == 0x02]
    if len(ints) < 9:
        raise ValueError("not an RSA private key")
    names = ("version", "n", "e", "d", "p", "q", "dp", "dq", "qinv")
    return dict(zip(names, ints[:9]))


def _cert_subject_and_key_id(cert_pem):
    """From a certificate: its subject Name (raw DER, reused verbatim as the
    issuer of what it signs) and the key id of its public key."""
    _tag, cert, _end = _read_tlv(_unpem(cert_pem))
    tbs = _children(_children(cert)[0][1])
    if tbs[0][0] == 0xA0:                                  # [0] version
        tbs = tbs[1:]
    # serial, signature, issuer, validity, subject, subjectPublicKeyInfo
    subject_raw = tbs[4][2]
    spki = _children(tbs[5][1])
    public_key_bits = spki[1][1][1:]                       # drop the unused-bits byte
    return subject_raw, hashlib.sha1(public_key_bits).digest()


# -------------------------------------------------------------------- RSA --
_SMALL_PRIMES = [p for p in range(3, 2000, 2)
                 if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]


def _probably_prime(n, rounds=64):
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits):
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _probably_prime(c):
            return c


def generate_rsa(bits=2048):
    e = 65537
    while True:
        p, q = _random_prime(bits // 2), _random_prime(bits // 2)
        n = p * q
        phi = (p - 1) * (q - 1)
        if p == q or n.bit_length() != bits or math.gcd(e, phi) != 1:
            continue
        d = pow(e, -1, phi)
        return {"version": 0, "n": n, "e": e, "d": d, "p": p, "q": q,
                "dp": d % (p - 1), "dq": d % (q - 1), "qinv": pow(q, -1, p)}


def _public_key_der(key):
    return _sequence(_integer(key["n"]), _integer(key["e"]))


def _spki(key):
    return _sequence(_sequence(_oid(OID_RSA), _NULL), _bit_string(_public_key_der(key)))


def private_key_pem(key):
    pkcs1 = _sequence(*(_integer(key[k]) for k in
                        ("version", "n", "e", "d", "p", "q", "dp", "dq", "qinv")))
    pkcs8 = _sequence(_integer(0), _sequence(_oid(OID_RSA), _NULL), _octet_string(pkcs1))
    return _pem("PRIVATE KEY", pkcs8)


def _sign_sha256(key, data):
    """PKCS#1 v1.5 signature: 00 01 FF..FF 00 DigestInfo, then RSA."""
    t = _SHA256_PREFIX + hashlib.sha256(data).digest()
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


# ------------------------------------------------------------------- X.509 --
def _name(cn, org, country):
    def rdn(oid, value):
        return _set(_sequence(_oid(oid), value))
    return _sequence(rdn(OID_C, _printable(country)), rdn(OID_O, _utf8(org)),
                     rdn(OID_CN, _utf8(cn)))


def _extension(oid, value_der, critical=False):
    # `critical` DEFAULTs to FALSE, and DER leaves a default value out.
    parts = [_oid(oid)] + ([_boolean(True)] if critical else []) + [_octet_string(value_der)]
    return _sequence(*parts)


def _key_usage(ca):
    # Named bits, MSB first: digitalSignature=0, keyEncipherment=2,
    # keyCertSign=5, cRLSign=6. DER drops trailing zero bits.
    return _bit_string(b"\x06", unused=1) if ca else _bit_string(b"\xa0", unused=5)


def _certificate(tbs, signing_key):
    return _sequence(tbs, _sequence(_oid(OID_SHA256_RSA), _NULL),
                     _bit_string(_sign_sha256(signing_key, tbs)))


def _tbs(serial, issuer_raw, subject_raw, days, key, extensions):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    not_before = now - datetime.timedelta(minutes=5)       # phones with a slow clock
    not_after = now + datetime.timedelta(days=days)
    return _sequence(
        _explicit(0, _integer(2)),                         # v3
        _integer(serial),
        _sequence(_oid(OID_SHA256_RSA), _NULL),
        issuer_raw,
        _sequence(_utc_time(not_before), _utc_time(not_after)),
        subject_raw,
        _spki(key),
        _explicit(3, _sequence(*extensions)))


def make_ca(cn="PoGO Private CA", org="PoGO Private Server", country="US", days=3650):
    """-> (private key PEM, certificate PEM) for a new root CA."""
    key = generate_rsa()
    name = _name(cn, org, country)
    key_id = hashlib.sha1(_public_key_der(key)).digest()
    exts = [
        _extension(OID_BASIC_CONSTRAINTS, _sequence(_boolean(True)), critical=True),
        _extension(OID_KEY_USAGE, _key_usage(ca=True), critical=True),
        _extension(OID_SUBJECT_KEY_ID, _octet_string(key_id)),
    ]
    tbs = _tbs(secrets.randbits(63), name, name, days, key, exts)
    return private_key_pem(key), _pem("CERTIFICATE", _certificate(tbs, key))


def make_leaf(ca_key_pem, ca_cert_pem, cn, sans, org="PoGO Private Server",
              country="US", days=397):
    """-> (private key PEM, certificate PEM) for a server cert signed by the
    CA. `sans` is the list of DNS names it must cover; `cn` is only cosmetic."""
    ca_key = load_private_key(ca_key_pem)
    issuer_raw, ca_key_id = _cert_subject_and_key_id(ca_cert_pem)
    key = generate_rsa()
    exts = [
        _extension(OID_BASIC_CONSTRAINTS, _sequence()),
        _extension(OID_KEY_USAGE, _key_usage(ca=False), critical=True),
        _extension(OID_EXT_KEY_USAGE, _sequence(_oid(OID_SERVER_AUTH))),
        _extension(OID_SUBJECT_ALT_NAME,
                   _sequence(*(_tlv(0x82, h.encode("ascii")) for h in sans))),
        _extension(OID_SUBJECT_KEY_ID,
                   _octet_string(hashlib.sha1(_public_key_der(key)).digest())),
        _extension(OID_AUTHORITY_KEY_ID, _sequence(_tlv(0x80, ca_key_id))),
    ]
    tbs = _tbs(secrets.randbits(63), issuer_raw, _name(cn, org, country), days, key, exts)
    return private_key_pem(key), _pem("CERTIFICATE", _certificate(tbs, ca_key))
