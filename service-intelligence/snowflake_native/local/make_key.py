"""Creates the key pair for the VELA_LOCAL Snowflake user (run once).

    python local/make_key.py

Writes the PRIVATE key to ~/.snowflake/vela_local_key.p8 (keep it on this computer only) and prints the PUBLIC
key line to paste into sql/30_local_vela_reader.sql (RSA_PUBLIC_KEY = '...').
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

folder = Path.home() / ".snowflake"
folder.mkdir(exist_ok=True)
key_file = folder / "vela_local_key.p8"
if key_file.exists():
    raise SystemExit(f"{key_file} already exists; delete it first if you really want a new key.")
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
try:
    key_file.chmod(0o600)
except OSError:
    pass
pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
body = "".join(line for line in pub.splitlines() if "-----" not in line)
print(f"Private key saved to: {key_file}\n\nPaste this into the SQL script (RSA_PUBLIC_KEY):\n\n{body}\n")
