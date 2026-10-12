"""Create the key pair Tesla requires (one-time).

    python make_keys.py

Writes:
  private-key.pem  -> keep secret, stays in this folder
  public_site/.well-known/appspecific/com.tesla.3p.public-key.pem  -> upload to your site
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

HERE = Path(__file__).parent
priv_path = HERE / "private-key.pem"
pub_path = HERE / "public_site/.well-known/appspecific/com.tesla.3p.public-key.pem"

if priv_path.exists():
    raise SystemExit("private-key.pem already exists - not overwriting it.")

key = ec.generate_private_key(ec.SECP256R1())  # = prime256v1, as Tesla requires
priv_path.write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
))
pub_path.parent.mkdir(parents=True, exist_ok=True)
pub_path.write_bytes(key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
))
# GitHub Pages hides folders starting with "." unless this file exists
(HERE / "public_site/.nojekyll").write_text("")

print("Created", priv_path.name, "(keep secret)")
print("Created", pub_path.relative_to(HERE), "(upload the public_site folder contents)")
