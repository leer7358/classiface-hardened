# utils/crypto.py
import os
import base64
import struct
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# -----------------------------
# Key management
# -----------------------------
# ✅ AES-256 key: 32 bytes, stored in ENV as base64
# PowerShell (example):
#   setx EMBED_KEY "vHEG/wzxIdctjN9vMPvnvGMPrvRKRMV+bv37zH8iSRc="
#
# IMPORTANT:
# - If you rotate/change EMBED_KEY, you cannot decrypt old records unless you migrate them.
# - Keep this key secret (do NOT commit it to GitHub).

EMBED_VERSION = 1
NONCE_LEN = 12  # AES-GCM standard nonce length


def _load_embed_key_from_dotenv() -> None:
    if os.environ.get("EMBED_KEY", "").strip():
        return

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() != "EMBED_KEY":
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            os.environ["EMBED_KEY"] = value
        return


def _get_key() -> bytes:
    _load_embed_key_from_dotenv()
    k_b64 = os.environ.get("EMBED_KEY", "").strip()
    if not k_b64:
        raise RuntimeError("Missing EMBED_KEY env var (base64-encoded 32 bytes).")

    try:
        key = base64.b64decode(k_b64, validate=True)
    except Exception as e:
        raise RuntimeError("EMBED_KEY is not valid base64.") from e

    if len(key) != 32:
        raise RuntimeError(f"EMBED_KEY must decode to 32 bytes (got {len(key)}).")

    return key


# -----------------------------
# Embedding packing/unpacking
# -----------------------------
def _validate_embedding_list(emb_list):
    if not isinstance(emb_list, list):
        raise ValueError("Embedding must be a Python list.")
    if len(emb_list) != 128:
        raise ValueError(f"Embedding must have length 128 (got {len(emb_list)}).")

    # ensure values are numeric (floatable)
    for i, x in enumerate(emb_list):
        try:
            float(x)
        except Exception as e:
            raise ValueError(f"Embedding element at index {i} is not a number: {x}") from e


def embedding_to_bytes(emb_list) -> bytes:
    """
    128 floats -> 512 bytes (float32 little-endian)
    """
    _validate_embedding_list(emb_list)
    return struct.pack("<128f", *[float(x) for x in emb_list])


def bytes_to_embedding(b: bytes) -> list:
    """
    512 bytes -> 128 floats
    """
    if not isinstance(b, (bytes, bytearray)):
        raise ValueError("Input must be bytes.")
    if len(b) != 512:
        raise ValueError(f"Embedding bytes must be exactly 512 bytes (got {len(b)}).")

    vals = struct.unpack("<128f", b)
    return [float(x) for x in vals]


# -----------------------------
# Encrypt / Decrypt
# -----------------------------
def encrypt_embedding(emb_list) -> dict:
    """
    Returns:
      {
        "ct": "<base64 ciphertext+tag>",
        "nonce": "<base64 nonce>",
        "v": 1
      }
    """
    key = _get_key()
    aes = AESGCM(key)

    nonce = os.urandom(NONCE_LEN)
    pt = embedding_to_bytes(emb_list)

    # AAD (associated data) optional; you can bind to a purpose string if you want:
    # aad = b"embeddings_v1"
    aad = None

    ct = aes.encrypt(nonce, pt, aad)

    return {
        "ct": base64.b64encode(ct).decode("utf-8"),
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "v": EMBED_VERSION,
    }


def decrypt_embedding(obj: dict) -> list:
    """
    Accepts dict like:
      {"ct": "...", "nonce": "...", "v": 1}
    Returns embedding list of 128 floats.
    """
    if not isinstance(obj, dict):
        raise ValueError("Encrypted embedding must be a dict.")

    v = obj.get("v", None)
    if v != EMBED_VERSION:
        raise ValueError(f"Unsupported embedding encryption version: {v}")

    if "ct" not in obj or "nonce" not in obj:
        raise ValueError("Encrypted embedding dict must contain 'ct' and 'nonce'.")

    try:
        ct = base64.b64decode(obj["ct"])
        nonce = base64.b64decode(obj["nonce"])
    except Exception as e:
        raise ValueError("ct/nonce are not valid base64.") from e

    if len(nonce) != NONCE_LEN:
        raise ValueError(f"Nonce must be {NONCE_LEN} bytes (got {len(nonce)}).")

    key = _get_key()
    aes = AESGCM(key)

    aad = None
    pt = aes.decrypt(nonce, ct, aad)

    return bytes_to_embedding(pt)
