#!/usr/bin/env python3
"""TriCascade: lock text and files with three national cipher standards at once.

Layer 1  Kuznyechik (Russia, GOST R 34.12-2015)   CTR mode
Layer 2  SM4        (China,  GB/T 32907-2016)     CTR mode
Layer 3  AES-256    (USA,    FIPS 197)            GCM mode (authenticates everything)

One passphrase -> scrypt -> HKDF -> three independent keys. No cipher internals are
mixed or modified: each layer is a standard algorithm used as specified.
Run `python tricascade.py` to open the local app. Nothing leaves your computer.
"""
import argparse, base64, getpass, hashlib, json, math, os, secrets, sys, threading
import unicodedata, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import gostcrypto

MAGIC, SALT_LEN, NONCE_LEN, PAD_TO = b"TC1", 16, 12, 32
BEGIN, END = "-----BEGIN TRICASCADE MESSAGE-----", "-----END TRICASCADE MESSAGE-----"


class DecryptError(Exception):
    pass


# ---------- core ----------
def derive_keys(password, salt):
    pw = unicodedata.normalize("NFC", password).encode()
    master = hashlib.scrypt(pw, salt=salt, n=2**15, r=8, p=1, maxmem=2**27, dklen=32)
    sub = lambda label, n: HKDF(hashes.SHA256(), n, salt, label).derive(master)
    return {
        "kuz": sub(b"tc1/kuznyechik/key", 32), "kuz_nonce": sub(b"tc1/kuznyechik/nonce", 8),
        "sm4": sub(b"tc1/sm4/key", 16), "sm4_iv": sub(b"tc1/sm4/iv", 16),
        "aes": sub(b"tc1/aes256gcm/key", 32),
    }


def _kuznyechik_ctr(data, key, nonce8):
    cipher = gostcrypto.gostcipher.new("kuznechik", bytearray(key), gostcrypto.gostcipher.MODE_ECB)
    out = bytearray()
    for i in range(0, len(data), 16):
        stream = bytes(cipher.encrypt(bytearray(nonce8 + (i // 16).to_bytes(8, "big"))))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], stream))
    return bytes(out)


def _sm4_ctr(data, key, iv):
    c = Cipher(algorithms.SM4(key), modes.CTR(iv)).encryptor()
    return c.update(data) + c.finalize()


def _pad(b):
    return b + b"\x80" + b"\x00" * ((-len(b) - 1) % PAD_TO)


def _unpad(b):
    b = b.rstrip(b"\x00")
    if not b.endswith(b"\x80"):
        raise DecryptError("Damaged message.")
    return b[:-1]


def lock_bytes(data, password):
    """Returns (locked_bytes, layer_sizes)."""
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    k = derive_keys(password, salt)
    padded = _pad(data)
    l1 = _kuznyechik_ctr(padded, k["kuz"], k["kuz_nonce"])
    l2 = _sm4_ctr(l1, k["sm4"], k["sm4_iv"])
    header = MAGIC + salt + nonce
    l3 = AESGCM(k["aes"]).encrypt(nonce, l2, header)
    sizes = [len(data), len(l1), len(l2), len(header) + len(l3)]
    return header + l3, sizes


def unlock_bytes(blob, password):
    h = len(MAGIC) + SALT_LEN + NONCE_LEN
    if len(blob) < h + 16 or blob[:3] != MAGIC:
        raise DecryptError("This is not a TriCascade message.")
    salt, nonce = blob[3:3 + SALT_LEN], blob[3 + SALT_LEN:h]
    k = derive_keys(password, salt)
    try:
        l2 = AESGCM(k["aes"]).decrypt(nonce, blob[h:], blob[:h])
    except InvalidTag:
        raise DecryptError("Wrong passphrase, or the message was changed.") from None
    l1 = _sm4_ctr(l2, k["sm4"], k["sm4_iv"])
    return _unpad(_kuznyechik_ctr(l1, k["kuz"], k["kuz_nonce"]))


def armor(blob):
    b = base64.b64encode(blob).decode()
    return "\n".join([BEGIN] + [b[i:i + 64] for i in range(0, len(b), 64)] + [END])


def dearmor(text):
    body = "".join(l.strip() for l in text.strip().splitlines() if not l.startswith("-----"))
    try:
        return base64.b64decode("".join(body.split()), validate=True)
    except Exception:
        raise DecryptError("The locked text looks incomplete or damaged.") from None


def lock_text(text, password):
    blob, sizes = lock_bytes(text.encode("utf-8"), password)
    return armor(blob), sizes


def unlock_text(text, password):
    return unlock_bytes(dearmor(text), password).decode("utf-8")


# ---------- passphrases ----------
CONS, VOWELS = "bdfghjklmnprstvwzc", "aeiou"
BITS_PER_SYLLABLE = math.log2(len(CONS) * len(VOWELS))


def make_passphrase(words=5, syllables=3):
    word = lambda: "".join(secrets.choice(CONS) + secrets.choice(VOWELS) for _ in range(syllables))
    return "-".join(word() for _ in range(words)), round(words * syllables * BITS_PER_SYLLABLE)


def make_password(length=20):
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!#$%&*+-=?@"
    return "".join(secrets.choice(alphabet) for _ in range(length)), round(length * math.log2(len(alphabet)))


def rate(pw):
    pool = sum(n for n, ok in ((26, any(c.islower() for c in pw)), (26, any(c.isupper() for c in pw)),
                               (10, any(c.isdigit() for c in pw)), (33, any(not c.isalnum() for c in pw))) if ok)
    bits = len(pw) * math.log2(pool) if pool else 0
    if bits < 50:
        return "Weak: use the generator or a longer passphrase."
    return "Fair." if bits < 75 else "Strong."


# ---------- self-test with published test vectors ----------
def selftest():
    res = []
    def check(name, fn):
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        res.append({"name": name, "ok": ok})

    ecb = lambda a, k, p: Cipher(a(k), modes.ECB()).encryptor().update(p)
    k4 = bytes.fromhex("0123456789abcdeffedcba9876543210")
    kk = bytes.fromhex("8899aabbccddeeff0011223344556677fedcba98765432100123456789abcdef")
    check("AES-256 matches FIPS 197 Appendix C.3", lambda: ecb(algorithms.AES, bytes(range(32)), bytes.fromhex(
        "00112233445566778899aabbccddeeff")).hex() == "8ea2b7ca516745bfeafc49904b496089")
    check("SM4 matches the GB/T 32907 example", lambda: ecb(algorithms.SM4, k4, k4).hex() == "681edf34d206965e86b3e94f536e4246")
    check("Kuznyechik matches RFC 7801 example", lambda: bytes(gostcrypto.gostcipher.new(
        "kuznechik", bytearray(kk), gostcrypto.gostcipher.MODE_ECB).encrypt(bytearray.fromhex(
        "1122334455667700ffeeddccbbaa9988"))).hex() == "7f679d90bebc24305a468d42b9d4edcd")
    sample = "Meet at 9. Bring the 책 and the café receipt."
    box, _ = lock_text(sample, "correct horse")
    check("Lock and unlock round trip", lambda: unlock_text(box, "correct horse") == sample)
    def wrong():
        try: unlock_text(box, "wrong"); return False
        except DecryptError: return True
    check("Wrong passphrase is rejected", wrong)
    def tamper():
        raw = bytearray(dearmor(box)); raw[-1] ^= 1
        try: unlock_bytes(bytes(raw), "correct horse"); return False
        except DecryptError: return True
    check("Any changed byte is detected", tamper)
    return res


# ---------- local web app ----------
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TriCascade</title><style>
:root{--bg:#eef1f4;--panel:#fff;--ink:#15212b;--mute:#566470;--line:#c9d2da;--l1:#2f5d8a;--l2:#a2453b;--l3:#2d6a4f;--btn:#15212b;--btnt:#fff;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#10171d;--panel:#18222b;--ink:#e6edf2;--mute:#97a6b2;--line:#2c3a46;--l1:#7fb0e0;--l2:#e08a80;--l3:#7fcfa6;--btn:#e6edf2;--btnt:#10171d;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 18px 60px}h1{font:700 2rem/1.1 Georgia,"Times New Roman",serif;margin:0 0 6px}
.sub{color:var(--mute);margin:0 0 22px;max-width:60ch}nav{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:20px}
nav button{background:none;border:0;border-bottom:3px solid transparent;color:var(--mute);font:inherit;padding:8px 12px;cursor:pointer}
nav button[aria-selected=true]{color:var(--ink);border-color:var(--ink);font-weight:600}
section{display:none}section.on{display:block}label{display:block;font-weight:600;margin:14px 0 4px}
textarea,input{width:100%;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:10px;font:inherit}
textarea{min-height:130px;resize:vertical}.mono{font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
.row{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}button.b{background:var(--btn);color:var(--btnt);border:0;border-radius:6px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}
button.b.alt{background:none;color:var(--ink);border:1px solid var(--ink)}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--l1);outline-offset:2px}
.out{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px;min-height:60px;white-space:pre-wrap;margin-top:6px}
.err{color:var(--l2);font-weight:600}.hint{color:var(--mute);font-size:.9rem}
.layers{display:flex;gap:6px;margin:12px 0;flex-wrap:wrap}.layers div{flex:1 1 150px;border-left:6px solid;padding:6px 10px;background:var(--panel);border-radius:0 6px 6px 0;font-size:.9rem}
.layers b{display:block}.t{padding:8px 0;border-bottom:1px solid var(--line)}.ok{color:var(--l3);font-weight:700}.no{color:var(--l2);font-weight:700}
h2{font-size:1.15rem;margin:22px 0 6px}code{background:var(--panel);padding:1px 5px;border-radius:4px}
</style></head><body><main>
<h1>TriCascade</h1><p class="sub">Lock a message with three national cipher standards at once, using one passphrase. Everything runs on this computer.</p>
<nav role="tablist"><button aria-selected="true" data-t="lock">Lock and unlock</button><button aria-selected="false" data-t="gen">Password maker</button><button aria-selected="false" data-t="how">How it works</button><button aria-selected="false" data-t="test">Self-test</button></nav>
<section id="lock" class="on"><label for="msg">Message or locked text</label><textarea id="msg" placeholder="Type a message to lock, or paste locked text to unlock."></textarea>
<label for="pw">Passphrase</label><input id="pw" type="password" autocomplete="off"><p class="hint" id="rate"></p>
<div class="row"><button class="b" id="bl">Lock message</button><button class="b alt" id="bu">Unlock message</button></div>
<div class="layers" id="layers" hidden></div><label>Result</label><div class="out mono" id="res" aria-live="polite"></div>
<div class="row"><button class="b alt" id="cp">Copy result</button></div></section>
<section id="gen"><p>Random passphrases are easy to say and hard to guess. Use one as your lock passphrase, and store it in a password manager.</p>
<div class="row"><button class="b" id="g1">New passphrase</button><button class="b alt" id="g2">New password</button></div><div class="out mono" id="gout"></div><p class="hint" id="gbits"></p></section>
<section id="how"><h2>Three layers, three keys</h2><p>Your passphrase is stretched with <code>scrypt</code>, then split by HKDF into three unrelated keys. Your message passes through:</p>
<div class="layers"><div style="border-color:var(--l1)"><b>Layer 1: Kuznyechik</b>Russia, GOST R 34.12-2015</div><div style="border-color:var(--l2)"><b>Layer 2: SM4</b>China, GB/T 32907-2016</div><div style="border-color:var(--l3)"><b>Layer 3: AES-256-GCM</b>USA, FIPS 197</div></div>
<p>The outer AES layer also seals the whole package, so any changed character is caught before unlocking starts. Messages are padded so their exact length is blurred.</p>
<h2>What this is good for</h2><p>Notes, recovery codes, and messages you send over ordinary chat or email, where both people know the passphrase. Share the passphrase another way, such as in person or by phone.</p>
<h2>What it cannot do</h2><p>It cannot protect a weak passphrase, a device with malware, or someone who looks over your shoulder. It has no forward secrecy, and it does not prove who sent a message. The code is open but has not had an independent security audit, so do not use it for life-or-death secrets. Check that encryption software is legal where you live.</p></section>
<section id="test"><p>Checks each cipher against published test vectors, then tries a round trip, a wrong passphrase, and a tampered message.</p>
<div class="row"><button class="b" id="rt">Run self-test</button></div><div id="tout"></div></section>
</main><script>
const T="__TOKEN__",$=s=>document.querySelector(s);
const api=async(p,b)=>(await fetch("/api/"+p,{method:"POST",headers:{"Content-Type":"application/json","X-TC-Token":T},body:JSON.stringify(b||{})})).json();
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll("nav button").forEach(x=>x.setAttribute("aria-selected",x==b));
document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id==b.dataset.t))});
const show=(r,ok)=>{$("#res").className="out mono"+(ok?"":" err");$("#res").textContent=r};
async function go(kind){const text=$("#msg").value,password=$("#pw").value;$("#layers").hidden=true;
if(!text||!password){show("Enter a message and a passphrase first.",false);return}
show("Working...",true);const r=await api(kind,{text,password});if(r.error){show(r.error,false);return}
show(r.result,true);if(r.sizes){const n=["Layer 1: Kuznyechik","Layer 2: SM4","Layer 3: AES-256-GCM"],c=["--l1","--l2","--l3"];
$("#layers").innerHTML=n.map((x,i)=>`<div style="border-color:var(${c[i]})"><b>${x}</b>${r.sizes[i+1]} bytes</div>`).join("");$("#layers").hidden=false}}
$("#bl").onclick=()=>go("lock");$("#bu").onclick=()=>go("unlock");
$("#pw").oninput=async e=>{$("#rate").textContent=e.target.value?(await api("rate",{password:e.target.value})).result:""};
$("#cp").onclick=async()=>{await navigator.clipboard.writeText($("#res").textContent);$("#cp").textContent="Copied";setTimeout(()=>$("#cp").textContent="Copy result",1500)};
const gen=async k=>{const r=await api(k);$("#gout").textContent=r.result;$("#gbits").textContent="About "+r.bits+" bits of randomness."};
$("#g1").onclick=()=>gen("passphrase");$("#g2").onclick=()=>gen("password");
$("#rt").onclick=async()=>{$("#tout").textContent="Running...";const r=await api("selftest");
$("#tout").innerHTML=r.results.map(x=>`<div class="t"><span class="${x.ok?"ok":"no"}">${x.ok?"Pass":"Fail"}</span> ${x.name}</div>`).join("")};
</script></body></html>"""


def run_app(open_browser=True):
    token = secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'"); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path == "/" and self._host_ok():
                self._send(200, PAGE.replace("__TOKEN__", token), "text/html")
            else:
                self._send(404, "{}")
        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-TC-Token") != token:
                return self._send(403, "{}")
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                route, pw = self.path[5:], d.get("password", "")
                if route == "lock":
                    text, sizes = lock_text(d["text"], pw); out = {"result": text, "sizes": sizes}
                elif route == "unlock": out = {"result": unlock_text(d["text"], pw)}
                elif route == "rate": out = {"result": rate(pw)}
                elif route in ("passphrase", "password"):
                    v, bits = make_passphrase() if route == "passphrase" else make_password(); out = {"result": v, "bits": bits}
                elif route == "selftest": out = {"results": selftest()}
                else: return self._send(404, "{}")
            except DecryptError as e: out = {"error": str(e)}
            except Exception: out = {"error": "Something went wrong. Check your input and try again."}
            self._send(200, json.dumps(out))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"TriCascade is running at {url}\nPress Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")


# ---------- command line ----------
def main():
    p = argparse.ArgumentParser(description="TriCascade: Kuznyechik + SM4 + AES-256 cascade.")
    sub = p.add_subparsers(dest="cmd")
    for name in ("lock", "unlock"):
        s = sub.add_parser(name, help=f"{name} text (-t or stdin) or a file (-i/-o)")
        s.add_argument("-t", "--text"); s.add_argument("-i", "--infile"); s.add_argument("-o", "--outfile")
    g = sub.add_parser("gen", help="make a random passphrase or password"); g.add_argument("--password", action="store_true")
    sub.add_parser("selftest", help="run built-in test vectors"); sub.add_parser("app", help="open the local web app (default)")
    a = p.parse_args()
    if a.cmd in (None, "app"): return run_app()
    if a.cmd == "gen":
        v, bits = make_password() if a.password else make_passphrase(); print(v); print(f"(about {bits} bits)", file=sys.stderr); return
    if a.cmd == "selftest":
        r = selftest()
        for x in r: print(("PASS  " if x["ok"] else "FAIL  ") + x["name"])
        sys.exit(0 if all(x["ok"] for x in r) else 1)
    pw = getpass.getpass("Passphrase: ")
    try:
        if a.cmd == "lock" and a.infile:
            blob, _ = lock_bytes(open(a.infile, "rb").read(), pw); open(a.outfile or a.infile + ".tc", "wb").write(blob)
        elif a.cmd == "unlock" and a.infile:
            data = unlock_bytes(open(a.infile, "rb").read(), pw)
            if a.outfile: open(a.outfile, "wb").write(data)
            else: sys.stdout.buffer.write(data)
        else:
            text = a.text if a.text is not None else sys.stdin.read()
            print(lock_text(text, pw)[0] if a.cmd == "lock" else unlock_text(text, pw))
    except DecryptError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
