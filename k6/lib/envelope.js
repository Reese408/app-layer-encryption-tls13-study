// Client side of the AES-256-GCM envelope (WebCrypto). Mirrors app/crypto_envelope.py:
//   envelope = {"v":1,"kid":..,"n":b64(nonce[12]),"ct":b64(ciphertext||tag[16])}
//   AAD      = "v1|<direction>|<METHOD>|<path>|<kid>"
// The client seals with the c2s key and opens with the s2c key. Key material is never logged.
import encoding from 'k6/encoding';

const te = new TextEncoder();

export async function importKeys(keyFileText) {
  const doc = JSON.parse(keyFileText);
  const imp = (b64) =>
    crypto.subtle.importKey('raw', encoding.b64decode(b64, 'std'), { name: 'AES-GCM' }, false, [
      'encrypt',
      'decrypt',
    ]);
  return { kid: doc.kid, c2s: await imp(doc.c2s), s2c: await imp(doc.s2c) };
}

export function aad(direction, method, path, kid) {
  return te.encode(`v1|${direction}|${method}|${path}|${kid}`);
}

// plaintext: ArrayBuffer. Returns the envelope JSON string. A fresh random nonce per call.
export async function seal(keys, plaintext, aadBytes) {
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: nonce, additionalData: aadBytes, tagLength: 128 },
    keys.c2s,
    plaintext,
  );
  return JSON.stringify({
    v: 1,
    kid: keys.kid,
    n: encoding.b64encode(nonce.buffer, 'std'),
    ct: encoding.b64encode(ct, 'std'),
  });
}

// Returns the plaintext as a base64 string (compared byte-exactly against the fixture).
export async function openToB64(keys, envelopeText, aadBytes) {
  const env = JSON.parse(envelopeText);
  if (env.v !== 1 || env.kid !== keys.kid) throw new Error('invalid_envelope');
  const pt = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: encoding.b64decode(env.n, 'std'), additionalData: aadBytes, tagLength: 128 },
    keys.s2c,
    encoding.b64decode(env.ct, 'std'),
  );
  return encoding.b64encode(pt, 'std');
}

// Returns the plaintext as an ArrayBuffer (Phase 4: the response is parsed, not compared).
export async function openBytes(keys, envelopeText, aadBytes) {
  const env = JSON.parse(envelopeText);
  if (env.v !== 1 || env.kid !== keys.kid) throw new Error('invalid_envelope');
  return crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: encoding.b64decode(env.n, 'std'), additionalData: aadBytes, tagLength: 128 },
    keys.s2c,
    encoding.b64decode(env.ct, 'std'),
  );
}
