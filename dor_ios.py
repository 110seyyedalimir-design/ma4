# -*- coding: utf-8 -*-
"""
دوربرگردون برای آیفون — سیستم اشتراک زمان‌دار بدون برنامهٔ اختصاصی

هر اشتراک آیفون یک «توکن» شخصی دارد و همه‌چیز روی سرور کنترل می‌شود:

  /dns-query/<token>                  DNS-over-HTTPS شخصی (RFC 8484، GET و POST)
                                       فعال → جواب واقعی از DNSهای بدون فیلتر (با کش و ECS)
                                       تمام‌شده / قطع‌شده → REFUSED
  /ios/p/<token>                      «پروب» پروفایل آیفون: 200 فقط وقتی اشتراک معتبر است.
                                       پروفایل با OnDemandRules فقط وقتی پروب 200 بدهد DNS ما را روشن می‌کند؛
                                       پس با تمام شدن اشتراک (یا خاموشی سرور) آیفون به DNS عادی برمی‌گردد.
  /ios/add/<token>                    صفحهٔ نصب فارسی + وضعیت اشتراک
  /ios/profile/<token>.mobileconfig   پروفایل DNS آیفون (بدون برنامه)
  /ios/sb/<token>.json                راه دوم: پروفایل برنامهٔ رایگان sing-box (VPN واقعی + ترفند TLS)

The hot path never takes db.lock (the bot holds it while talking to Telegram).
"""
import base64
import html
import ipaddress
import json
import logging
import plistlib
import re
import secrets
import threading
import time
import urllib.parse
import uuid

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.rcode
import requests

import dor_jdate as J

log = logging.getLogger("ios")

CFG = {
    "db": None,                 # bot DB object (uses db.data["ios"])
    "public_url": "",           # https://xxx.hf.space   (or IOS_PUBLIC_URL)
    "bot_username": "dorbargardoon_bot",
    "event": lambda ev: None,   # callback for alerts (queued and sent by the bot)
    "max_ips": 3,               # distinct networks within an hour before a sharing alert
    "auto_block": lambda: False,
    "upstreams": [
        "https://dns.google/dns-query",
        "https://cloudflare-dns.com/dns-query",
        "https://basic.rethinkdns.com/dns-query",
        "https://doh.sb/dns-query",
        "https://dns.quad9.net/dns-query",
    ],
}
APP_NAME = "دوربرگردون"
SINGBOX_APPSTORE = "https://apps.apple.com/app/sing-box-mt/id6785326793"   # sing-box MT (1.14+; VT was removed in 2026)

STATE = {"queries": 0, "cache_hits": 0, "upstream_errors": 0, "refused": 0, "upstream_ok": None,
         "last_upstream": "", "last_error": "", "day": "", "today": 0, "dirty": False, "urgent": False}
_lock = threading.Lock()


def configure(**kw):
    CFG.update({k: v for k, v in kw.items() if v is not None})


def data():
    dbo = CFG["db"] or (CFG.get("get_db") or (lambda: None))()
    d = dbo.data
    io = d.get("ios")
    if not isinstance(io, dict):
        io = d["ios"] = {}
    io.setdefault("subs", {})
    io.setdefault("trials", {})
    io.setdefault("counter", 0)
    io.setdefault("log", [])
    return io


def base_url():
    return (CFG["public_url"] or "").rstrip("/")


def host():
    return urllib.parse.urlparse(base_url()).netloc


def _now():
    return int(time.time())


# ------------------------------------------------------------------ subscription model
def new_token():
    return re.sub(r"[-_]", "x", secrets.token_urlsafe(18))


def create_sub(uid=None, customer="", note="", source="bot"):
    io = data()
    io["counter"] += 1
    sub = {"token": new_token(), "serial": f"I-{io['counter']:04d}", "uid": uid, "customer": customer,
           "note": note, "created": _now(), "first_use": None, "expires": None, "pending": 0,
           "unlimited": False, "trial": False, "revoked": False, "revoke_reason": "", "replaced_by": None,
           "queries": 0, "refused": 0, "last_seen": 0, "last_refused": 0, "ips": {}, "via": "",
           "notified": {}, "share_alert": 0, "history": [], "source": source}
    io["subs"][sub["token"]] = sub
    STATE["dirty"] = True
    return sub


def history(sub, action, detail="", by=""):
    h = sub.setdefault("history", [])
    h.append({"at": _now(), "a": action, "d": detail, "by": by})
    del h[:-40]


def state(sub, now=None):
    """revoked | active | pending | expired"""
    now = now or time.time()
    if sub.get("revoked") or sub.get("replaced_by"):
        return "revoked"
    if sub.get("unlimited"):
        return "active"
    exp = sub.get("expires")
    if exp and now < exp:
        return "active"
    if not exp and sub.get("pending", 0) > 0:
        return "pending"
    return "expired"


def usable(sub, now=None):
    return state(sub, now) in ("active", "pending")


def grant(sub, kind, value, by="", note=""):
    """kind: dur (seconds) | until (timestamp) | unl | trial (seconds). Returns a Persian description."""
    now = _now()
    st = state(sub, now)
    if kind == "unl":
        sub["unlimited"], sub["pending"], sub["trial"] = True, 0, False
        desc = "نامحدود"
    elif kind == "until":
        cur = sub["expires"] if (st == "active" and sub.get("expires")) else 0
        if st == "pending":
            cur = now + sub["pending"]
        sub["expires"], sub["pending"], sub["trial"] = max(int(value), int(cur)), 0, False
        desc = f"تا {J.jalali(sub['expires'], False)}"
    else:
        value = int(value)
        sub["trial"] = kind == "trial"
        if st == "active" and sub.get("expires"):
            sub["expires"] += value                      # running → extend the end
        elif st == "pending":
            sub["pending"] += value                      # not started yet → add
        else:
            sub["expires"], sub["pending"] = None, value  # new / expired → starts at next use
        desc = ("تست " if kind == "trial" else "") + J.dur(value)
    sub["notified"] = {}
    history(sub, "grant", desc + (f" — {note}" if note else ""), by)
    io = data()
    io["log"].append({"serial": sub["serial"], "uid": sub.get("uid"), "kind": kind, "value": value, "desc": desc,
                      "at": now, "by": by, "customer": sub.get("customer", ""), "note": note})
    del io["log"][:-5000]
    STATE["dirty"] = True
    return desc


def revoke(sub, reason="", by=""):
    sub["revoked"], sub["revoke_reason"], sub["revoked_at"] = True, reason, _now()
    history(sub, "revoke", reason, by)
    STATE["dirty"] = True


def unrevoke(sub, by=""):
    sub["revoked"], sub["revoke_reason"] = False, ""
    history(sub, "unrevoke", "", by)
    STATE["dirty"] = True


def reset_token(sub, by=""):
    """New secret link with the same serial and time; the old link stops working immediately."""
    io = data()
    new = json.loads(json.dumps(sub))
    new.update(token=new_token(), ips={}, share_alert=0, replaced_by=None)
    history(new, "reset", f"لینک قبلی (…{sub['token'][-4:]}) باطل شد", by)
    sub["replaced_by"], sub["revoked_at"] = new["token"], _now()
    io["subs"][new["token"]] = new
    STATE["dirty"] = True
    return new


def current_subs(uid=None):
    subs = [s for s in list(data()["subs"].values()) if not s.get("replaced_by")]
    if uid is not None:
        subs = [s for s in subs if s.get("uid") == uid]
    return sorted(subs, key=lambda s: s["created"])


def by_serial(serial):
    serial = (serial or "").strip().upper()
    for s in current_subs():
        if s["serial"].upper() == serial:
            return s
    return None


def resolve_token(tok):
    """Follows replaced_by (for display only; the DNS path never follows it)."""
    io = data()
    s = io["subs"].get(tok)
    seen = 0
    while s and s.get("replaced_by") and s["replaced_by"] in io["subs"] and seen < 20:
        s = io["subs"][s["replaced_by"]]
        seen += 1
    return s


def find(q):
    q = (q or "").strip()
    if not q:
        return []
    s = by_serial(q)
    if s:
        return [s]
    m = re.search(r"([A-Za-z0-9]{20,})", q)
    if m and m.group(1) in data()["subs"]:
        return [resolve_token(m.group(1))]
    ql = q.lower().lstrip("@")
    return [s for s in current_subs() if (ql and ql in (s.get("customer") or "").lower().lstrip("@"))
            or (ql and ql in (s.get("note") or "").lower()) or ql == str(s.get("uid"))]


def describe(sub, now=None):
    now = now or time.time()
    st = state(sub, now)
    if st == "revoked":
        return "⛔️ قطع‌شده" + (f" ({sub['revoke_reason']})" if sub.get("revoke_reason") else "")
    if sub.get("unlimited"):
        return "✅ فعال — نامحدود"
    if st == "pending":
        return f"🕓 آماده — {'تست ' if sub.get('trial') else ''}{J.dur(sub['pending'])} از اولین اتصال"
    if st == "active":
        return f"✅ فعال تا {J.jalali(sub['expires'])} ({J.left(sub['expires'] - now)} مانده)"
    return "⌛️ تمام شده" + (f" در {J.jalali(sub['expires'])}" if sub.get("expires") else "")


def links(sub):
    b, t = base_url(), sub["token"]
    return {"page": f"{b}/ios/add/{t}", "profile": f"{b}/ios/profile/{t}.mobileconfig",
            "doh": f"{b}/dns-query/{t}", "probe": f"{b}/ios/p/{t}", "singbox": f"{b}/ios/sb/{t}.json"}


def singbox_import_url(sub):
    return ("sing-box://import-remote-profile?url=" + urllib.parse.quote(links(sub)["singbox"], safe="")
            + "#" + urllib.parse.quote(f"{APP_NAME} {sub['serial']}"))


def counts():
    now = time.time()
    c = {"active": 0, "pending": 0, "expired": 0, "revoked": 0}
    online = 0
    for s in current_subs():
        c[state(s, now)] += 1
        if now - s.get("last_seen", 0) < 900:
            online += 1
    return c, online


def ip_nets(sub, window=3600, now=None):
    now = now or time.time()
    return [n for n, t in list((sub.get("ips") or {}).items()) if now - t < window]


# ------------------------------------------------------------------ DNS resolver
_S = requests.Session()
_S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=64, max_retries=0))
_CACHE = {}
_CACHE_MAX = 30000
_up_fail = {}


def _client_net(ip):
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if not a.is_global:
        return None
    return ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 56}", strict=False)


def _net_key(ip):
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return "?"
    return str(ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False))


def _min_ttl(msg):
    ttls = [rr.ttl for sec in (msg.answer, msg.authority) for rr in sec]
    return max(5, min(min(ttls), 1800)) if ttls else 30


def _upstream(wire):
    ups = CFG["upstreams"]
    now = time.time()
    order = sorted(range(len(ups)), key=lambda i: (_up_fail.get(i, 0) > now, i))
    last = None
    for i in order[:3]:
        try:
            r = _S.post(ups[i], data=wire, timeout=4,
                        headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"})
            if r.status_code == 200 and r.content:
                STATE["upstream_ok"], STATE["last_upstream"] = True, ups[i]
                _up_fail.pop(i, None)
                return r.content
            last = f"{ups[i]} HTTP {r.status_code}"
        except Exception as e:
            last = f"{ups[i]} {type(e).__name__}"
        _up_fail[i] = now + 60
        STATE["upstream_errors"] += 1
    STATE["upstream_ok"], STATE["last_error"] = False, str(last)
    raise IOError(last)


def _refused(q_wire):
    try:
        q = dns.message.from_wire(q_wire)
        r = dns.message.make_response(q)
        r.set_rcode(dns.rcode.REFUSED)
        return r.to_wire()
    except Exception:
        return b""


def resolve(q_wire, client_ip=""):
    """Returns the wire answer for a wire query (cached, with EDNS Client Subnet for nearby CDN nodes)."""
    q = dns.message.from_wire(q_wire)
    if not q.question:
        raise ValueError("no question")
    qq = q.question[0]
    net = _client_net(client_ip)
    key = (qq.name.to_text().lower(), qq.rdtype, qq.rdclass, str(net) if net else "", bool(q.flags & dns.flags.CD))
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        STATE["cache_hits"] += 1
        r = dns.message.from_wire(hit[1])
        left = max(1, int(hit[0] - now))
        for sec in (r.answer, r.authority):
            for rr in sec:
                rr.ttl = min(rr.ttl, left)
        r.id = q.id
        return r.to_wire()
    do = bool(q.ednsflags & dns.flags.DO)
    fwd = dns.message.make_query(qq.name, qq.rdtype, qq.rdclass)
    fwd.flags = q.flags
    opts = [o for o in (q.options or []) if not isinstance(o, dns.edns.ECSOption) and int(o.otype) not in (10, 12)]
    if net:
        opts.append(dns.edns.ECSOption(str(net.network_address), net.prefixlen, 0))
    fwd.use_edns(0, dns.flags.DO if do else 0, 1232, options=opts)
    fwd.id = 0
    ans = _upstream(fwd.to_wire())
    r = dns.message.from_wire(ans)
    ttl = _min_ttl(r) if r.rcode() in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN) else 0
    if ttl:
        if len(_CACHE) > _CACHE_MAX:
            for k in [k for k, v in list(_CACHE.items()) if v[0] <= now][:_CACHE_MAX // 2] or list(_CACHE)[:_CACHE_MAX // 3]:
                _CACHE.pop(k, None)
        _CACHE[key] = (now + ttl, ans)
    r.id = q.id
    return r.to_wire()


def _count_query(sub, ip, via):
    now = _now()
    sub["queries"] = sub.get("queries", 0) + 1
    sub["last_seen"] = now
    if via:
        sub["via"] = via
    nk = _net_key(ip)
    ips = sub.setdefault("ips", {})
    new_net = nk not in ips
    ips[nk] = now
    if new_net:
        for k in [k for k, t in list(ips.items()) if now - t > 86400]:
            ips.pop(k, None)
        nets = ip_nets(sub, 3600, now)
        if len(nets) > CFG["max_ips"] and now - sub.get("share_alert", 0) > 6 * 3600:
            sub["share_alert"] = now
            auto = False
            try:
                auto = bool(CFG["auto_block"]())
            except Exception:
                pass
            if auto:
                revoke(sub, "استفادهٔ هم‌زمان از چند شبکه (قطع خودکار)", "auto")
            CFG["event"]({"type": "share", "token": sub["token"], "nets": nets, "auto": auto})
    day = time.strftime("%Y-%m-%d")
    if STATE["day"] != day:
        STATE["day"], STATE["today"] = day, 0
    STATE["today"] += 1
    STATE["dirty"] = True


# Chrome/Edge extension: it looks up v-<nonce>.<VERIFY_DOMAIN> and then asks /ios/api/<token>?n=<nonce>
# whether that lookup reached this token -> proves the browser really uses this personal DNS.
VERIFY_DOMAIN = "dorbargardoon-verify.net"
_VERIFY_RE = re.compile(r"^v-([a-z0-9]{8,32})\." + re.escape(VERIFY_DOMAIN) + r"\.?$")
# Still resolved when a link is expired/replaced, so the extension (and the user) can reach the bot to renew.
_GRACE = ("hf.space", "huggingface.co", "t.me", "telegram.me", "telegram.org")


def _qname(q_wire):
    try:
        q = dns.message.from_wire(q_wire)
        return q, (q.question[0].name.to_text().lower() if q.question else "")
    except Exception:
        return None, ""


def _grace_name(name):
    n = name.rstrip(".")
    hs = [h.split(":")[0] for h in (host(),) if h] + list(_GRACE)
    return any(n == h or n.endswith("." + h) for h in hs)


def _nx(q):
    r = dns.message.make_response(q)
    r.set_rcode(dns.rcode.NXDOMAIN)
    return r.to_wire()


def _record_verify(sub, nonce):
    now = _now()
    v = sub.setdefault("verify", {})
    v[nonce] = now
    for k in [k for k, t in list(v.items()) if now - t > 3600]:
        v.pop(k, None)
    while len(v) > 20:
        v.pop(min(v, key=v.get), None)
    sub["chrome_seen"] = now
    STATE["dirty"] = True


def handle_doh(token, q_wire, client_ip, ua=""):
    """Returns (http_status, content_type, body)."""
    STATE["queries"] += 1
    sub = data()["subs"].get(token)
    if not sub:
        return 404, "text/plain", b"unknown"
    q, name = _qname(q_wire)
    m = _VERIFY_RE.match(name) if name else None
    if m:
        _record_verify(sub, m.group(1))
        return 200, "application/dns-message", _nx(q)
    now = time.time()
    st = state(sub, now)
    if st not in ("active", "pending") and name and _grace_name(name):
        try:
            return 200, "application/dns-message", resolve(q_wire, client_ip)
        except Exception:
            return 200, "application/dns-message", _refused(q_wire)
    if st == "pending":
        with _lock:
            if state(sub, now) == "pending":
                sub["first_use"] = _now()
                sub["expires"] = _now() + int(sub["pending"])
                sub["pending"] = 0
                history(sub, "start", "اولین اتصال")
                STATE["urgent"] = True
                CFG["event"]({"type": "start", "token": token})
        st = "active"
    if st != "active":
        sub["refused"] = sub.get("refused", 0) + 1
        sub["last_refused"] = _now()
        STATE["refused"] += 1
        return 200, "application/dns-message", _refused(q_wire)
    _count_query(sub, client_ip, "singbox" if "sing-box" in ua.lower() or "sfi" in ua.lower() else "profile")
    try:
        return 200, "application/dns-message", resolve(q_wire, client_ip)
    except (ValueError, dns.exception.DNSException):
        return 400, "text/plain", b"bad dns query"
    except Exception as e:
        log.warning("upstream failed: %s", e)
        try:
            q = dns.message.from_wire(q_wire)
            r = dns.message.make_response(q)
            r.set_rcode(dns.rcode.SERVFAIL)
            return 200, "application/dns-message", r.to_wire()
        except Exception:
            return 502, "text/plain", b"upstream error"


# ------------------------------------------------------------------ profiles
def mobileconfig(sub):
    L = links(sub)
    ns = uuid.UUID("6f1c2b8e-8d4b-4e1a-9a55-d0b0a6c1e001")
    inner_uuid = str(uuid.uuid5(ns, sub["token"] + ":dns")).upper()
    outer_uuid = str(uuid.uuid5(ns, sub["token"])).upper()
    ident = f"com.dorbargardoon.dns.{sub['serial'].lower()}"
    dns_payload = {
        "PayloadType": "com.apple.dnsSettings.managed",
        "PayloadIdentifier": ident + ".settings",
        "PayloadUUID": inner_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": APP_NAME,
        "PayloadDescription": f"{APP_NAME} — DNS رمزنگاری‌شده ({sub['serial']})",
        "DNSSettings": {"DNSProtocol": "HTTPS", "ServerURL": L["doh"]},
        # Our DNS is used only while the subscription is valid (probe returns 200).
        # Expired / revoked / server down → the phone falls back to its normal DNS (no dead internet).
        "OnDemandRules": [
            {"Action": "Connect", "URLStringProbe": L["probe"]},
            {"Action": "Disconnect"},
        ],
        "ProhibitDisablement": False,
    }
    prof = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": ident,
        "PayloadUUID": outer_uuid,
        "PayloadDisplayName": APP_NAME,
        "PayloadDescription": f"پروفایل {APP_NAME} برای آیفون — اشتراک {sub['serial']}. "
                              f"برای تمدید یا پشتیبانی: @{CFG['bot_username']}",
        "PayloadOrganization": "Dorbargardoon",
        "PayloadRemovalDisallowed": False,
        "PayloadScope": "System",
        "PayloadContent": [dns_payload],
    }
    return plistlib.dumps(prof, fmt=plistlib.FMT_XML, sort_keys=False)


def _sb_version(ua):
    m = re.search(r"sing-box[ /v]*(\d+)\.(\d+)", ua or "", re.I) or re.search(r"SF[IAM]/(\d+)\.(\d+)", ua or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def singbox_config(sub, ua="", force=None):
    """Remote profile for the free sing-box iOS app. Format depends on the app version (User-Agent)."""
    L = links(sub)
    ver = force or _sb_version(ua) or (1, 14)   # current App Store app (sing-box MT) is 1.14+; old format only if the app says so
    u = urllib.parse.urlparse(L["doh"])
    tun = {"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
           "auto_route": True, "strict_route": True}
    rules = [{"action": "sniff"},
             {"protocol": "dns", "action": "hijack-dns"},
             {"ip_is_private": True, "outbound": "direct"}]
    if ver >= (1, 12):
        dns_cfg = {"servers": [{"type": "https", "tag": "dorb", "server": u.hostname, "path": u.path,
                                "domain_resolver": "local"},
                               {"type": "local", "tag": "local"}],
                   "final": "dorb", "strategy": "prefer_ipv4"}
        # Intra-style trick: split the TLS ClientHello so SNI filters can't read it
        rules.append({"protocol": "tls", "action": "route-options", "tls_fragment": True})
        route = {"rules": rules, "final": "direct", "auto_detect_interface": True, "default_domain_resolver": "local"}
    else:
        dns_cfg = {"servers": [{"tag": "dorb", "address": L["doh"], "address_resolver": "local"},
                               {"tag": "local", "address": "local"}],
                   "rules": [{"outbound": "any", "server": "local"}],
                   "final": "dorb", "strategy": "prefer_ipv4"}
        route = {"rules": rules, "final": "direct", "auto_detect_interface": True}
    return {"log": {"level": "warn"}, "dns": dns_cfg, "inbounds": [tun],
            "outbounds": [{"type": "direct", "tag": "direct"}], "route": route}


# ------------------------------------------------------------------ install page
_CSS = """*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'SF Pro Text',Vazirmatn,Tahoma,sans-serif;
background:linear-gradient(160deg,#5b3dff 0%,#3f6bff 45%,#1fb9e8 100%);min-height:100vh;color:#0c1230;line-height:1.85}
.w{max-width:560px;margin:auto;padding:22px 16px 40px}.hd{color:#fff;text-align:center;margin:10px 0 18px}
.logo{width:74px;height:74px;border-radius:20px;background:rgba(255,255,255,.18);display:inline-flex;align-items:center;
justify-content:center;border:1px solid rgba(255,255,255,.5)}h1{margin:8px 0 0;font-size:28px}.sub{opacity:.9}
.card{background:#fff;border-radius:22px;padding:18px;margin:14px 0;box-shadow:0 10px 30px rgba(10,20,80,.18)}
.st{font-weight:700;font-size:17px}.ok{color:#0a8f4d}.bad{color:#c62828}.warn{color:#b26a00}
.btn{display:block;text-align:center;text-decoration:none;font-weight:700;font-size:18px;padding:15px;border-radius:16px;
margin:10px 0;color:#fff;background:linear-gradient(135deg,#6c47ff,#22a7eb)}.btn2{background:#eef1ff;color:#3a3fbf}
ol{padding-right:20px;margin:6px 0;list-style-type:persian}.en{white-space:nowrap;unicode-bidi:isolate}li{margin:6px 0}small,.m{color:#5b6280;font-size:14px}code{direction:ltr;
display:inline-block;background:#f1f3fb;padding:1px 6px;border-radius:6px}h2{font-size:18px;margin:4px 0 8px}
details summary{cursor:pointer;font-weight:700}"""

_ICON = ('<svg width="44" height="44" viewBox="0 0 100 100"><path d="M64 84V40a22 22 0 0 0-44 0v12" fill="none" '
         'stroke="#fff" stroke-width="11" stroke-linecap="round"/><path d="M6 48h28L20 70z" fill="#fff" '
         'stroke="#fff" stroke-width="3" stroke-linejoin="round"/></svg>')


def install_page(sub):
    L = links(sub)
    now = time.time()
    st = state(sub, now)
    bot = CFG["bot_username"]
    e = html.escape
    if st == "active":
        cls = "ok"
    elif st == "pending":
        cls = "ok"
    else:
        cls = "bad"
    status = e(describe(sub, now))
    seen = ""
    if sub.get("last_seen"):
        ago = now - sub["last_seen"]
        seen = (f"<div class='m'>📶 آخرین اتصال گوشی شما: {e(J.ago(sub['last_seen'], now))}"
                + (" — <b class='ok'>پروفایل فعال است ✅</b>" if ago < 300 else "") + "</div>")
    renew = f"https://t.me/{bot}"
    if st in ("expired", "revoked"):
        body = f"""<div class="card"><div class="st bad">{status}</div>
<p>برای تمدید یا دریافت اشتراک جدید به ربات بروید:</p><a class="btn" href="{renew}">🤖 <span class="en" dir="ltr">@{bot}</span></a>
<details><summary>اینترنت عادی من برنگشته؟</summary><ol><li>یک بار «حالت هواپیما» را روشن و خاموش کنید.</li>
<li>یا پروفایل را حذف کنید: Settings ← General ← VPN &amp; Device Management ← {APP_NAME} ← Remove Profile.</li></ol></details></div>"""
    else:
        body = f"""<div class="card"><div class="st {cls}">{status}</div>{seen}
<div class="m">🔖 شمارهٔ اشتراک: <b>{e(sub['serial'])}</b></div></div>

<div class="card"><h2>📲 روش ساده (پیشنهادی) — بدون نصب برنامه</h2>
<a class="btn" href="{e(L['profile'])}">نصب پروفایل {APP_NAME}</a>
<ol><li>روی دکمهٔ بالا بزنید و <b>Allow</b> را بزنید.<br><small>اگر این صفحه داخل تلگرام باز شده، اول از منوی ⋯ «Open in Safari» را بزنید.</small></li>
<li>به <b>Settings</b> (تنظیمات) بروید؛ بالای صفحه <b>Profile Downloaded</b> را بزنید.</li>
<li><b>Install</b> ← رمز گوشی ← دوباره <b>Install</b> ← <b>Done</b>.</li>
<li>تمام! سایت‌ها و برنامه‌ها باز می‌شوند. اگر نشد، یک بار حالت هواپیما را روشن و خاموش کنید.</li></ol>
<small>⚠️ پیام «Unverified» یا «امضا نشده» طبیعی است؛ پروفایل فقط DNS را تنظیم می‌کند و به اطلاعات شما دسترسی ندارد.</small></div>

<div class="card"><h2>⚡ حالت قوی‌تر — با برنامهٔ رایگان <span class="en">sing-box</span></h2>
<p class="m">VPN واقعی با ترفند ضدفیلتر (مثل نسخهٔ اندروید). اگر روش ساده برای بعضی سایت‌ها کافی نبود از این استفاده کنید.</p>
<ol><li>برنامهٔ رایگان <b>sing-box MT</b> را از App Store نصب کنید (رابط فارسی هم دارد):</li></ol>
<a class="btn btn2" href="{SINGBOX_APPSTORE}">📥 دریافت <span class="en">sing-box</span> از App Store</a>
<ol start="2"><li>برگردید همین صفحه و این دکمه را بزنید (پروفایل خودکار اضافه می‌شود):</li></ol>
<a class="btn" href="{e(singbox_import_url(sub))}">➕ افزودن {APP_NAME} به <span class="en">sing-box</span></a>
<ol start="3"><li>در sing-box به تب Dashboard بروید، پروفایل «{APP_NAME}» را انتخاب و دکمهٔ شروع را بزنید؛ اجازهٔ VPN را بدهید.</li></ol>
<small>برای قطع: همان دکمه را در sing-box خاموش کنید. هم‌زمان با روش ساده هم مشکلی ندارد. در این حالت فقط آدرس سایت‌ها (DNS) از سرور ما رد می‌شود؛ خود اینترنت و سرعت شما مستقیم است.</small></div>

<div class="card"><details><summary>🛡 آیا این پروفایل امن است و ضرری ندارد؟</summary><ul>
<li>فقط تنظیم <b>DNS</b> است (مثل دفترچهٔ تلفن اینترنت). به عکس‌ها، پیام‌ها، رمزها، مخاطبین یا فایل‌های شما هیچ دسترسی ندارد و چیزی روی گوشی نصب یا کنترل نمی‌کند.</li>
<li>به <b>تماس، پیامک، سیم‌کارت و تنظیمات اپراتور</b> دست نمی‌زند و باتری مصرف نمی‌کند.</li>
<li>اگر اشتراک تمام شود یا سرور در دسترس نباشد، آیفون خودش به DNS عادی برمی‌گردد. در بدترین حالت یک بار حالت هواپیما را روشن و خاموش کنید.</li>
<li>هر وقت خواستید با یک لمس حذف می‌شود و گوشی دقیقاً مثل قبل می‌شود.</li>
<li>ما فهرست سایت‌هایی را که باز می‌کنید ذخیره نمی‌کنیم.</li></ul></details>
<details><summary>🗑 حذف پروفایل</summary>
<p>Settings ← General ← VPN &amp; Device Management ← {APP_NAME} ← Remove Profile</p></details>
<details><summary>💬 پشتیبانی و تمدید</summary><p>از ربات <a href="{renew}"><span class="en" dir="ltr">@{bot}</span></a> ← «🍏 آیفون».</p></details></div>"""
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{APP_NAME} برای آیفون</title><style>{_CSS}</style></head><body><div class="w">
<div class="hd"><div class="logo">{_ICON}</div><h1>{APP_NAME}</h1><div class="sub">اینترنت آزاد روی آیفون، با یک لمس</div></div>
{body}</div></body></html>""".encode("utf-8")


def not_found_page():
    bot = CFG["bot_username"]
    return (f"""<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{APP_NAME}</title><style>{_CSS}</style><div class="w"><div class="hd"><div class="logo">{_ICON}</div><h1>{APP_NAME}</h1></div>
<div class="card"><div class="st bad">این لینک معتبر نیست یا عوض شده است.</div>
<p>لینک جدید را از ربات بگیرید:</p><a class="btn" href="https://t.me/{bot}">🤖 <span class="en" dir="ltr">@{bot}</span> ← 🍏 آیفون</a></div></div></html>""").encode("utf-8")


def api_status(tok, query=""):
    """JSON status for the browser extension. The token itself is the secret, so CORS is open."""
    hdr = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
           "Access-Control-Allow-Origin": "*"}
    sub = data()["subs"].get(tok)
    if not sub:
        return 404, hdr, json.dumps({"ok": False, "state": "unknown", "bot": CFG["bot_username"]}).encode()
    now = _now()
    n = (urllib.parse.parse_qs(query or "").get("n") or [""])[0].lower()
    seen = None
    if re.match(r"^[a-z0-9]{8,32}$", n):
        seen = n in (sub.get("verify") or {})
    Lk = links(sub)
    out = {"ok": True, "serial": sub["serial"], "state": state(sub, now), "trial": bool(sub.get("trial")),
           "unlimited": bool(sub.get("unlimited")), "expires": int(sub.get("expires") or 0),
           "pending": int(sub.get("pending") or 0), "now": now, "doh": Lk["doh"], "page": Lk["page"],
           "bot": CFG["bot_username"], "seen": seen, "replaced": bool(sub.get("replaced_by")),
           "last_seen": int(sub.get("last_seen") or 0), "verify_domain": VERIFY_DOMAIN}
    return 200, hdr, json.dumps(out).encode()


# ------------------------------------------------------------------ HTTP entry point
def handle_http(method, path, query, headers, body, client_ip):
    """Returns (status, headers_dict, body_bytes) or None if the path isn't ours."""
    ua = headers.get("User-Agent", "") or headers.get("user-agent", "")
    m = re.match(r"^/dns-query/([A-Za-z0-9]{16,40})/?$", path)
    if m:
        tok = m.group(1)
        if method == "POST":
            q = body or b""
        else:
            b64 = (urllib.parse.parse_qs(query).get("dns") or [""])[0]
            try:
                q = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            except Exception:
                q = b""
        if not q or len(q) > 4096:
            return 400, {"Content-Type": "text/plain"}, b"missing dns query"
        code, ctype, out = handle_doh(tok, q, client_ip, ua)
        return code, {"Content-Type": ctype, "Cache-Control": "no-store"}, out
    m = re.match(r"^/ios/api/([A-Za-z0-9]{16,40})/?$", path)
    if m:
        return api_status(m.group(1), query)
    m = re.match(r"^/ios/(p|add|profile|sb)/([A-Za-z0-9]{16,40})(\.mobileconfig|\.json)?/?$", path)
    if not m:
        return None
    kind, tok = m.group(1), m.group(2)
    sub = data()["subs"].get(tok)
    if kind == "p":
        ok = bool(sub) and usable(sub)
        return (200 if ok else 404), {"Content-Type": "text/plain", "Cache-Control": "no-store"}, (b"ok" if ok else b"no")
    if not sub:
        return 404, {"Content-Type": "text/html; charset=utf-8"}, not_found_page()
    if sub.get("replaced_by"):
        return 410, {"Content-Type": "text/html; charset=utf-8"}, not_found_page()
    if kind == "add":
        return 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}, install_page(sub)
    if kind == "profile":
        history(sub, "profile", "دانلود پروفایل")
        return 200, {"Content-Type": "application/x-apple-aspen-config",
                     "Content-Disposition": f'attachment; filename="Dorbargardoon-{sub["serial"]}.mobileconfig"',
                     "Cache-Control": "no-store"}, mobileconfig(sub)
    if kind == "sb":
        exp = sub.get("expires") or 0
        hdr = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
               "Profile-Update-Interval": "6",
               "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={0 if sub.get('unlimited') else int(exp)}",
               "Content-Disposition": "inline; filename=\"Dorbargardoon.json\"; filename*=UTF-8''" + urllib.parse.quote(APP_NAME + ".json")}
        force = None
        v = (urllib.parse.parse_qs(query).get("v") or [""])[0]
        if re.match(r"^\d+\.\d+$", v):
            force = tuple(int(x) for x in v.split("."))
        return 200, hdr, json.dumps(singbox_config(sub, ua, force), ensure_ascii=False, indent=1).encode("utf-8")
    return None
