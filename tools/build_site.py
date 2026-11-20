"""
Builds a copy of the dashboard for a plain static host (GitHub Pages, a personal site):
the stored maps only, no scan or tree-height buttons, and by default a password.

    python tools/build_site.py OUT_DIR             view only, data encrypted with a password
    python tools/build_site.py OUT_DIR --no-lock   view only, readable by anyone with the link

The password is asked for twice, or read from the RIZIKI_PASSWORD environment variable.
Rebuilding into the same OUT_DIR with the same password keeps the lock, so devices that
already opened the map are not asked again. A different password makes every device ask.

WHAT IT WRITES
    OUT_DIR/                 the dashboard (no demo data); config.js set to VIEW_ONLY
    OUT_DIR/data/            every .json in dashboard/data, encrypted unless --no-lock
    OUT_DIR/data/lock.json   salt, rounds and a check value; nothing secret
    OUT_DIR/.nojekyll        if OUT_DIR is the site's root, GitHub Pages serves the files as they are
OUT_DIR must be new, empty, or made by this script before. OUT_DIR/data/ is replaced;
a data file that hasn't changed keeps its old encrypted copy, so nothing changes on the site.

HOW STRONG THE LOCK IS
    Each data file is AES-256-GCM encrypted with a key made from the password by
    PBKDF2-SHA256 (600,000 rounds). Someone who downloads the files gets nothing
    readable without the password. The page's code is public, anyone given the
    password can pass it on, and a short password can be guessed by trying many:
    use a phrase of several words. To shut someone out, rebuild with a new password.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
ROUNDS = 600_000
MIN_PASSWORD = 10
SKIP = {"data", "data_demo", "__pycache__", ".DS_Store"}
MARK = ".riziki-build"          # marks a folder this script wrote

b64 = lambda b: base64.b64encode(b).decode()


def aesgcm(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        sys.exit("Needs the cryptography package: pip install cryptography")
    return AESGCM(key)


def seal(key, data, squeeze=True):
    """One encrypted file, as lock.js reads it."""
    iv = os.urandom(12)
    body = zlib.compress(data, 9) if squeeze else data
    return {"riziki_locked": 1, "z": int(squeeze), "iv": b64(iv), "data": b64(aesgcm(key).encrypt(iv, body, None))}


def unseal(key, box):
    body = aesgcm(key).decrypt(base64.b64decode(box["iv"]), base64.b64decode(box["data"]), None)
    return zlib.decompress(body) if box.get("z") else body


def same(key, old_text, plain):
    """The old encrypted file if it holds exactly this content, else None."""
    try:
        return old_text if old_text and unseal(key, json.loads(old_text)) == plain else None
    except Exception:
        return None


def derive(password, salt, rounds):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds, 32)


def get_password():
    pw = os.environ.get("RIZIKI_PASSWORD")
    if pw is None:
        pw = getpass.getpass("Password for the map: ")
        if getpass.getpass("Same password again: ") != pw:
            sys.exit("The two passwords differ. Nothing written.")
    if len(pw) < MIN_PASSWORD:
        sys.exit(f"Use at least {MIN_PASSWORD} characters; a few words together is easiest. Nothing written.")
    return pw


def lock_for(password, old_lock):
    """The key and lock.json: the old ones if the password still opens them, else new."""
    if old_lock:
        try:
            salt = base64.b64decode(old_lock["salt"])
            key = derive(password, salt, old_lock["iter"])
            if unseal(key, old_lock["check"]) == b"riziki":
                return key, old_lock, False
        except Exception:
            pass
    salt = os.urandom(16)
    key = derive(password, salt, ROUNDS)
    lock = {"riziki_lock": 1, "kdf": "PBKDF2-SHA256", "iter": ROUNDS, "salt": b64(salt),
            "check": seal(key, b"riziki", squeeze=False)}
    return key, lock, True


def set_config(text, locked):
    """config.js for a static host: no API, view only, locked or not."""
    for name, value in (("API_URL", '""'), ("VIEW_ONLY", "true"), ("LOCKED", "true" if locked else "false")):
        text, n = re.subn(rf"(\n\s*{name}:\s*)[^,\n]+,", rf"\g<1>{value},", text)
        if n != 1:
            sys.exit(f"dashboard/config.js: expected one {name} setting, found {n}.")
    return text


def stamp_worker(out):
    """Give sw.js a version from the page's own files, so phones pick up every new build."""
    sw = out / "sw.js"
    text = sw.read_text()
    files = re.search(r"const FILES = \[(.*?)\];", text, re.S).group(1)
    h = hashlib.sha256()
    for name in re.findall(r'"([^"]+)"', files):
        p = out / (name if name != "./" else "index.html")
        if p.is_file():
            h.update(p.read_bytes())
    text, n = re.subn(r'const VERSION = "([^"-]+)(?:-[0-9a-f]+)?";', rf'const VERSION = "\g<1>-{h.hexdigest()[:8]}";', text)
    if n != 1:
        sys.exit("dashboard/sw.js: couldn't find the VERSION line.")
    sw.write_text(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="folder to write, e.g. ../my-site/riziki")
    ap.add_argument("--no-lock", action="store_true", help="don't encrypt: anyone with the link can see the maps")
    ap.add_argument("--force", action="store_true", help="write into a folder that holds other files")
    a = ap.parse_args(argv)

    out = a.out.resolve()
    if out == ROOT or out == DASH or DASH in out.parents or out in DASH.parents:
        sys.exit("Choose a folder outside this repo's dashboard, e.g. your site's repo.")
    if out.is_dir() and any(out.iterdir()) and not (out / MARK).exists() and not a.force:
        sys.exit(f"{out} already holds files this script didn't write. Pick a new or empty folder "
                 "(a sub-folder such as riziki/), or add --force.")
    data = DASH / "data"
    if not (data / "index.json").is_file():
        sys.exit("dashboard/data/index.json is missing. Run: python -m mapping.index rebuild")

    old_lock = None
    if (out / "data" / "lock.json").is_file():
        try:
            old_lock = json.loads((out / "data" / "lock.json").read_text())
        except ValueError:
            pass
    key = None
    if not a.no_lock:
        key, lock, new = lock_for(get_password(), old_lock)

    # The page.
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DASH, out, dirs_exist_ok=True, ignore=lambda d, names: [n for n in names if n in SKIP])
    (out / "config.js").write_text(set_config((DASH / "config.js").read_text(), locked=not a.no_lock))
    stamp_worker(out)
    (out / ".nojekyll").write_text("")
    (out / MARK).write_text("Written by tools/build_site.py in the Riziki repo. Rebuilt on every update.\n")

    # The data. A file whose content hasn't changed keeps its old encrypted copy,
    # so rebuilding an unchanged map changes nothing on the website.
    before = {}
    if key and (out / "data").is_dir():
        for f in (out / "data").rglob("*.json"):
            before[f.relative_to(out / "data")] = f.read_text()
    shutil.rmtree(out / "data", ignore_errors=True)
    n = skipped = 0
    for src in sorted(data.rglob("*")):
        if not src.is_file():
            continue
        dst = out / "data" / src.relative_to(data)
        if src.suffix != ".json":
            skipped += 1
            print(f"  left out (not a .json data file): {src.relative_to(ROOT)}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if key:
            plain = src.read_bytes()
            json.loads(plain)                                 # refuse to publish a broken file
            dst.write_text(same(key, before.get(src.relative_to(data)), plain)
                           or json.dumps(seal(key, plain), separators=(",", ":")))
        else:
            shutil.copyfile(src, dst)
        n += 1
    if key:
        (out / "data" / "lock.json").write_text(json.dumps(lock, indent=1))

    print(f"Wrote {out}")
    print(f"  {n} data files, {'encrypted' if key else 'NOT encrypted: anyone with the link can read them'}")
    if key:
        print("  New password: every device will ask for it." if new else "  Same password as before: nobody is asked again.")
    print("Next: commit and push that folder with your site.")


if __name__ == "__main__":
    main()
