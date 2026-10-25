# TriCascade

Lock text and files with **three national cipher standards at once**, using one passphrase.
Everything runs on your own computer. Nothing is uploaded.

| Layer | Cipher | Origin | Standard |
|---|---|---|---|
| 1 | Kuznyechik (CTR) | Russia | GOST R 34.12-2015, RFC 7801 |
| 2 | SM4 (CTR) | China | GB/T 32907-2016, ISO/IEC 18033-3 Amd 1 |
| 3 | AES-256 (GCM) | USA | FIPS 197 |

## Quick start

```bash
git clone <your-repo-url> && cd tricascade
pip install -r requirements.txt
python tricascade.py          # opens the app in your browser
```

Requires Python 3.9+. On the page: type a message and a passphrase, press **Lock message**,
and send the result. Your friend pastes it, enters the same passphrase, and presses **Unlock message**.

### Command line

```bash
python tricascade.py lock -t "Meet at 9"        # prints a locked block
python tricascade.py unlock                     # paste locked text, then Ctrl+D
python tricascade.py lock -i taxes.pdf -o taxes.pdf.tc
python tricascade.py unlock -i taxes.pdf.tc -o taxes.pdf
python tricascade.py gen                        # random passphrase (about 97 bits)
python tricascade.py gen --password             # random password
python tricascade.py selftest                   # official test vectors + tamper checks
```

File locking uses pure-Python Kuznyechik, so it is slow for files over a few MB.

## How it works

1. **Key stretching.** Your passphrase goes through `scrypt` (N=2^15, r=8, p=1) with a random 16-byte salt.
2. **Key splitting.** HKDF-SHA256 turns the result into three unrelated keys and nonces.
3. **Padding.** The message is padded to a multiple of 32 bytes to blur its exact length.
4. **Cascade.** Padded data goes through Kuznyechik, then SM4, then AES-256-GCM. The AES layer also
   authenticates the header, so a changed byte is rejected before anything is decrypted.

Output format: a `TC1` header (salt + nonce) followed by the AES-GCM ciphertext, shown as Base64 between
`-----BEGIN TRICASCADE MESSAGE-----` lines.

**Design rule: no homemade cryptography.** The three ciphers are used exactly as standardized, from
well-known libraries (`cryptography` and `gostcrypto`). Only the composition is new, and it is simple on purpose.

## Is a cascade stronger?

Honestly, AES-256 alone is already considered very strong. The cascade is *defense in depth*: if a
flaw were ever found in one layer, the other two still protect your data. It also lets you see how three
different design traditions (see the accompanying article) work side by side.

## Limits you should know

- Security depends mostly on your **passphrase**. Use `gen`, and keep it in a password manager.
- Share the passphrase through a different channel than the message.
- No forward secrecy and no sender identity: anyone with the passphrase can lock and unlock.
- Not independently audited. `gostcrypto` is pure Python and not hardened against side-channel attacks,
  so do not run it on a shared or untrusted machine.
- The app listens only on `127.0.0.1`, uses a per-run token, and rejects foreign Host headers.
- Encryption rules differ by country. Check your local law.

## Self-test

`python tricascade.py selftest` verifies AES-256 (FIPS 197 C.3), SM4 (GB/T 32907 example) and
Kuznyechik (RFC 7801 example), then checks a round trip, a wrong passphrase and a tampered message.

MIT licensed.
