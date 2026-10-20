// Loop-timed microbenchmark of client-side AES-256-GCM envelope cost in k6 (WebCrypto).
// k6 lacks performance.now(), so per-operation cost is total Date.now() time / N with N
// chosen so each measurement spans >= ~1 s. Single VU; runs inside the same k6 container.
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import { Trend } from 'k6/metrics';
import { importKeys, seal, openToB64, aad } from './lib/envelope.js';

const OUT_DIR = __ENV.OUT_DIR || '/results/adhoc';
const SIZES = ['1k', '16k', '256k'];
const N = { '1k': 20000, '16k': 5000, '256k': 400 };
const fixtures = Object.fromEntries(
  SIZES.map((s) => [s, JSON.parse(open(`./fixtures/payloads-${s}.json`)).items[0]]),
);
const keyText = open('/run/secrets/aead_keys');

const sealT = new Trend('client_seal_ms_per_op');
const openT = new Trend('client_open_ms_per_op');
const thresholds = {};
for (const s of SIZES) {
  thresholds[`client_seal_ms_per_op{size:${s}}`] = ['max>=0'];
  thresholds[`client_open_ms_per_op{size:${s}}`] = ['max>=0'];
}
export const options = { vus: 1, iterations: 1, thresholds, summaryTrendStats: ['avg', 'count'] };

export default async function () {
  const keys = await importKeys(keyText);
  const aC = aad('c2s', 'POST', '/v1/process', keys.kid);
  // Build server-direction envelopes with the s2c key so open() can be timed realistically.
  const sKeys = { kid: keys.kid, c2s: keys.s2c, s2c: keys.s2c };
  const aS = aad('s2c', 'POST', '/v1/process', keys.kid);
  for (const size of SIZES) {
    const pt = encoding.b64decode(fixtures[size].req_b64, 'std');
    const n = N[size];
    for (let i = 0; i < Math.min(200, n); i++) await seal(keys, pt, aC); // warm-up
    let t0 = Date.now();
    for (let i = 0; i < n; i++) await seal(keys, pt, aC);
    const sealMs = (Date.now() - t0) / n;
    const env = await seal(sKeys, pt, aS);
    t0 = Date.now();
    for (let i = 0; i < n; i++) await openToB64(keys, env, aS);
    const openMs = (Date.now() - t0) / n;
    sealT.add(sealMs, { size });
    openT.add(openMs, { size });
    console.log(`${size}: seal=${sealMs.toFixed(4)} ms/op open=${openMs.toFixed(4)} ms/op (n=${n})`);
  }
}

export function handleSummary(data) {
  const results = {};
  for (const s of SIZES) {
    results[s] = {
      n: N[s],
      seal_ms_per_op: data.metrics[`client_seal_ms_per_op{size:${s}}`].values.avg,
      open_ms_per_op: data.metrics[`client_open_ms_per_op{size:${s}}`].values.avg,
    };
  }
  const doc = { unit: 'ms/op', method: 'Date.now() total / N, single VU, after 200-op warm-up', results };
  return { [`${OUT_DIR}/client_crypto_bench.json`]: JSON.stringify(doc, null, 1), stdout: JSON.stringify(results) + '\n' };
}
