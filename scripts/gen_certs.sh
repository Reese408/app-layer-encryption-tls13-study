#!/usr/bin/env bash
# Generate a local private CA and an ECDSA P-256 server certificate for the lab.
# Output: certs/ (gitignored). Private keys are never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=certs
mkdir -p "$OUT"
umask 077

if [[ -f "$OUT/server.pem" && "${FORCE:-0}" != "1" ]]; then
  echo "certs/ already present (set FORCE=1 to regenerate)"; exit 0
fi

openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key"
openssl req -x509 -new -key "$OUT/ca.key" -sha256 -days 365 \
  -subj "/CN=AEAD Study Local CA" -out "$OUT/ca.pem" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"

openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/server.key"
openssl req -new -key "$OUT/server.key" -subj "/CN=api" -out "$OUT/server.csr"
cat > "$OUT/server.ext" <<EXT
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:api-plain,DNS:api-aead,DNS:localhost,IP:127.0.0.1
EXT
openssl x509 -req -in "$OUT/server.csr" -CA "$OUT/ca.pem" -CAkey "$OUT/ca.key" \
  -CAcreateserial -days 365 -sha256 -extfile "$OUT/server.ext" -out "$OUT/server.pem"
rm -f "$OUT/server.csr" "$OUT/server.ext" "$OUT/ca.srl"
# Certificates and keys must be readable by the non-root container user (bind mount, read-only).
chmod 644 "$OUT/ca.pem" "$OUT/server.pem" "$OUT/server.key"
openssl verify -CAfile "$OUT/ca.pem" "$OUT/server.pem"
