#!/usr/bin/env python3
"""TriCascade: lock text and files with three national cipher standards at once.

Layer 1  Kuznyechik (Russia, GOST R 34.12-2015)   CTR mode
Layer 2  SM4        (China,  GB/T 32907-2016)     CTR mode
Layer 3  AES-256    (USA,    FIPS 197)            GCM mode (authenticates everything)

One passphrase -> scrypt -> HKDF -> three independent keys. No cipher internals are
mixed or modified: each layer is a standard algorithm used as specified.
"""
import argparse, base64, getpass, hashlib, math, os, secrets, sys
import unicodedata
import streamlit as st

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


# ---------- Streamlit Web App UI ----------
def run_streamlit_app():
    st.set_page_config(page_title="TriCascade | SPC Research", page_icon="🔐", layout="centered")
    
    # 연구 시리즈 브랜딩 상단 노출
    st.caption("SPC Research Series · Symbolic Persona Coding")
    st.title("TriCascade")
    st.markdown("Lock a message with three national cipher standards at once, using one passphrase. Everything runs securely.")

    tabs = st.tabs(["Lock and unlock", "Password maker", "How it works", "Self-test"])

    with tabs[0]:
        msg = st.text_area("Message or locked text", placeholder="Type a message to lock, or paste locked text to unlock.")
        pw = st.text_input("Passphrase", type="password", autocomplete="off")
        
        if pw:
            st.caption(f"Password strength: {rate(pw)}")

        col1, col2 = st.columns(2)
        with col1:
            lock_btn = st.button("Lock message", use_container_width=True)
        with col2:
            unlock_btn = st.button("Unlock message", use_container_width=True)

        if lock_btn:
            if not msg or not pw:
                st.error("Enter a message and a passphrase first.")
            else:
                try:
                    text, sizes = lock_text(msg, pw)
                    st.success("Message locked successfully!")
                    st.code(text, language="text")
                    st.markdown("**Layer sizes:**")
                    st.markdown(f"- Layer 1 (Kuznyechik): {sizes[1]} bytes")
                    st.markdown(f"- Layer 2 (SM4): {sizes[2]} bytes")
                    st.markdown(f"- Layer 3 (AES-256-GCM): {sizes[3]} bytes")
                except Exception as e:
                    st.error(f"Error: {e}")

        if unlock_btn:
            if not msg or not pw:
                st.error("Enter locked text and a passphrase first.")
            else:
                try:
                    res = unlock_text(msg, pw)
                    st.success("Message unlocked successfully!")
                    st.code(res, language="text")
                except DecryptError as e:
                    st.error(f"Decryption error: {e}")
                except Exception as e:
                    st.error(f"Something went wrong: {e}")

    with tabs[1]:
        st.markdown("Random passphrases are easy to say and hard to guess. Use one as your lock passphrase, and store it in a password manager.")
        col1, col2 = st.columns(2)
        if col1.button("New passphrase", use_container_width=True):
            v, bits = make_passphrase()
            st.code(v)
            st.caption(f"About {bits} bits of randomness.")
        if col2.button("New password", use_container_width=True):
            v, bits = make_password()
            st.code(v)
            st.caption(f"About {bits} bits of randomness.")

    with tabs[2]:
        st.subheader("Three layers, three keys")
        st.markdown("Your passphrase is stretched with `scrypt`, then split by HKDF into three unrelated keys. Your message passes through:")
        st.markdown("- **Layer 1: Kuznyechik** (Russia, GOST R 34.12-2015)")
        st.markdown("- **Layer 2: SM4** (China, GB/T 32907-2016)")
        st.markdown("- **Layer 3: AES-256-GCM** (USA, FIPS 197)")
        st.markdown("The outer AES layer also seals the whole package, so any changed character is caught before unlocking starts.")

    with tabs[3]:
        st.markdown("Checks each cipher against published test vectors, then tries a round trip, a wrong passphrase, and a tampered message.")
        if st.button("Run self-test", use_container_width=True):
            results = selftest()
            for r in results:
                if r["ok"]:
                    st.success(f"PASS: {r['name']}")
                else:
                    st.error(f"FAIL: {r['name']}")


# ---------- command line & main ----------
def main():
    if len(sys.argv) > 1 and sys.argv[1] in ["lock", "unlock", "gen", "selftest"]:
        p = argparse.ArgumentParser(description="TriCascade: Kuznyechik + SM4 + AES-256 cascade.")
        sub = p.add_subparsers(dest="cmd")
        for name in ("lock", "unlock"):
            s = sub.add_parser(name, help=f"{name} text (-t or stdin) or a file (-i/-o)")
            s.add_argument("-t", "--text"); s.add_argument("-i", "--infile"); s.add_argument("-o", "--outfile")
        g = sub.add_parser("gen", help="make a random passphrase or password"); g.add_argument("--password", action="store_true")
        sub.add_parser("selftest", help="run built-in test vectors")
        a = p.parse_args()
        
        if a.cmd == "gen":
            v, bits = make_password() if a.password else make_passphrase()
            print(v); print(f"(about {bits} bits)", file=sys.stderr); return
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
    else:
        run_streamlit_app()


if __name__ == "__main__":
    main()