"""Dorbargardoon activation codes (BNM1 format) — identical to the app and the HTML generator.

Code  = "BNM1-" + base64url( payload[28] || signature[64] )
payload: ver(1)=1 | type(1) | device(10) | serial(u32) | issuedAt(u32) | value(u32) | activateBy(u32)  (big-endian)
signature: ECDSA P-256 / SHA-256, raw r||s (32+32 bytes)
types: 1 = duration (seconds, from activation), 2 = until (unix time), 3 = unlimited, 4 = trial (seconds)
"""
import base64
import json
import os
import re
import secrets
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.exceptions import InvalidSignature

T_DUR, T_UNTIL, T_UNL, T_TRIAL = 1, 2, 3, 4
TNAME = {1: "مدت‌دار", 2: "تا تاریخ", 3: "نامحدود", 4: "آزمایشی"}

# Public key compiled into the Android app — the private key given to the bot must match it.
APP_PUBLIC_X = "51dd0512c19149439f8d70c335eef69a16e0010fd3cb15310bf9004ef36f79ca"
APP_PUBLIC_Y = "e10f9384311c045903b585ab0f95cef19b68e6be39b01ec558c13548ee991448"
ALPH = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_RE = re.compile(r"BNM1-[A-Za-z0-9_\-]{100,}")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64u(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- device ID
def fmt_dev(b: bytes) -> str:
    v, acc, bits, bi = [], 0, 0, 0
    while len(v) < 15:
        if bits < 5:
            acc = ((acc << 8) | b[bi]) & 0xFFFF
            bi += 1
            bits += 8
        v.append((acc >> (bits - 5)) & 31)
        bits -= 5
    s = "".join(ALPH[x] for x in v)
    s += ALPH[sum((i + 1) * x for i, x in enumerate(v)) % 31]
    return f"{s[0:4]}-{s[4:8]}-{s[8:12]}-{s[12:16]}"


_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _decode16(s: str):
    s = s.upper().replace("O", "0").replace("I", "1").replace("L", "1")
    if len(s) != 16:
        raise ValueError(f"کد دستگاه باید ۱۶ کاراکتر باشد (الان {str(len(s)).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))} کاراکتر است).")
    v = []
    for ch in s:
        i = ALPH.find(ch)
        if i < 0:
            raise ValueError(f"کاراکتر نامعتبر در کد دستگاه: {ch}")
        v.append(i)
    if ALPH[sum((i + 1) * v[i] for i in range(15)) % 31] != s[15]:
        raise ValueError("کد دستگاه اشتباه است (رقم کنترل مطابقت ندارد). لطفاً کد را دقیقاً از داخل برنامه کپی کنید.")
    acc = 0
    for i in range(15):
        acc = (acc << 5) | v[i]
    acc <<= 5
    b = acc.to_bytes(10, "big")
    return b, fmt_dev(b)


_GROUPS = re.compile(r"(?<![0-9A-Za-z])([0-9A-Za-z]{4})[\s\-_]?([0-9A-Za-z]{4})[\s\-_]?([0-9A-Za-z]{4})[\s\-_]?([0-9A-Za-z]{4})(?![0-9A-Za-z])")


def parse_dev(text: str):
    """Returns (bytes, 'XXXX-XXXX-XXXX-XXXX') or raises ValueError with a Persian message.
    Accepts the ID alone or inside other text (e.g. 'Device ID: XXXX-XXXX-XXXX-XXXX')."""
    t = (text or "").translate(_FA_DIGITS).replace("\u200c", "").replace("\u200e", "").replace("\u200f", "")
    first_err = None
    for m in _GROUPS.finditer(t):
        try:
            return _decode16("".join(m.groups()))
        except ValueError as e:
            first_err = first_err or e
    if first_err:
        raise first_err
    s = re.sub(r"[\s\-_:]", "", t)
    return _decode16(s)


# ---------------------------------------------------------------- keys
class Signer:
    def __init__(self, secret: str):
        secret = (secret or "").strip()
        if not secret:
            raise ValueError("LICENSE_PRIVATE_KEY is empty")
        if secret.startswith("{"):
            secret = json.loads(secret)["d"]
        d = int(secret, 16)
        self.priv = ec.derive_private_key(d, ec.SECP256R1())
        nums = self.priv.public_key().public_numbers()
        self.x = format(nums.x, "064x")
        self.y = format(nums.y, "064x")
        self.pub = self.priv.public_key()
        self.matches_app = (self.x == APP_PUBLIC_X and self.y == APP_PUBLIC_Y)

    def sign(self, payload: bytes) -> bytes:
        der = self.priv.sign(payload, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def verify(self, payload: bytes, sig: bytes) -> bool:
        if len(sig) != 64:
            return False
        r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
        try:
            self.pub.verify(encode_dss_signature(r, s), payload, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False


def build_payload(typ, dev: bytes, serial, issued_at, value, activate_by) -> bytes:
    return bytes([1, typ]) + dev + struct.pack(">IIII", serial, issued_at, value, activate_by)


def make_code(signer: Signer, typ, dev: bytes, serial, issued_at, value, activate_by) -> str:
    for v in (serial, issued_at, value, activate_by):
        if not (0 <= v <= 0xFFFFFFFF):
            raise ValueError("مقدار خارج از محدوده است")
    p = build_payload(typ, dev, serial, issued_at, value, activate_by)
    return "BNM1-" + _b64u(p + signer.sign(p))


def read_code(signer: Signer, text: str):
    """Parses a code. Returns dict with 'valid' (signature ok) or raises ValueError."""
    s = re.sub(r"\s+", "", text or "")
    i = s.find("BNM1-")
    if i >= 0:
        s = s[i + 5:]
    m = re.match(r"[A-Za-z0-9_\-]+", s)
    s = m.group(0) if m else ""
    try:
        allb = _unb64u(s)
    except Exception:
        raise ValueError("فرمت کد نامعتبر است")
    if len(allb) != 92:
        raise ValueError("طول کد نامعتبر است")
    p, sig = allb[:28], allb[28:]
    serial, issued, value, act_by = struct.unpack(">IIII", p[12:28])
    return {
        "valid": signer.verify(p, sig),
        "ver": p[0], "type": p[1], "dev": p[2:12], "device": fmt_dev(p[2:12]),
        "serial": serial, "issuedAt": issued, "value": value, "activateBy": act_by,
        "code": "BNM1-" + s,
    }


def new_serial(existing: set) -> int:
    while True:
        s = struct.unpack(">I", secrets.token_bytes(4))[0]
        if s and s not in existing:
            return s
