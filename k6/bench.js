// Single benchmark scenario for both configurations; MODE selects plain or aead.
//
// Env: MODE=plain|aead  SIZE=1k|16k|256k  VUS  WARMUP_S  DURATION_S  RUN_ID  OUT_DIR  SMOKE=0|1
//      EXECUTOR=constant-vus|constant-arrival-rate  RATE (req/s, arrival-rate only)
//      SAMPLE_EVERY=K (0 = off)
//
// Executors:
//   constant-vus            closed loop: VUS virtual users, each with one request in flight.
//   constant-arrival-rate   open loop (separate experiment): RATE iterations/s are started on a
//                           fixed schedule from a pool of VUS pre-allocated VUs. An iteration
//                           that cannot start on time is counted in k6 `dropped_iterations`.
//
// Timing sources:
//   http_req_duration       k6 high-resolution timer: send -> last response byte (TLS + server)
//   iteration_duration      k6 high-resolution timer: end-to-end client iteration, including
//                           client-side encryption, request, decryption and integrity check
//   client_encrypt_ms /     exec.instance.currentTestRunDuration, a monotonic sub-microsecond
//   client_decrypt_ms       clock (the pilot used Date.now(), which has 1 ms resolution).
//   server_*_ms             Parsed from the server's Server-Timing header (perf_counter_ns)
//
// Warm-up: iterations that START before WARMUP_S are tagged phase=warmup and excluded.
//
// Per-request sample (SAMPLE_EVERY=K > 0): every measured iteration whose global index
// (exec.scenario.iterationInTest) is divisible by K is written to the log as one `S,` line.
// This is deterministic systematic sampling, about 1/K of measured requests. K is odd, hence
// coprime with the fixture pool size (4): with one VU (where the global and per-VU indices
// coincide) the sample therefore cycles through all payload items instead of locking to one.
// With several VUs the item depends on each VU's own iteration count and is mixed anyway.
// The sample is NOT the full request population; exact percentiles come from the summary.
import http from 'k6/http';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import { check } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { importKeys, seal, openToB64, aad } from './lib/envelope.js';

const MODE = __ENV.MODE || 'plain';
const SIZE = __ENV.SIZE || '1k';
const VUS = parseInt(__ENV.VUS || '1', 10);
const WARMUP_S = parseInt(__ENV.WARMUP_S || '5', 10);
const DURATION_S = parseInt(__ENV.DURATION_S || '20', 10);
const RUN_ID = __ENV.RUN_ID || `${MODE}-${SIZE}-c${VUS}`;
const OUT_DIR = __ENV.OUT_DIR || '/results/adhoc';
const SMOKE = __ENV.SMOKE === '1';
const EXECUTOR = __ENV.EXECUTOR || 'constant-vus';
const RATE = parseInt(__ENV.RATE || '0', 10);
const SAMPLE_EVERY = parseInt(__ENV.SAMPLE_EVERY || '0', 10);
const OPEN = EXECUTOR === 'constant-arrival-rate';
if (!['plain', 'aead'].includes(MODE)) throw new Error('MODE must be plain|aead');
if (!['constant-vus', 'constant-arrival-rate'].includes(EXECUTOR)) throw new Error('bad EXECUTOR');
if (OPEN && !(RATE > 0)) throw new Error('RATE required for constant-arrival-rate');

const PATH = '/v1/process';
const URL = `https://${MODE === 'plain' ? 'api-plain' : 'api-aead'}:8443${PATH}`;
const WARMUP_MS = WARMUP_S * 1000;

// Per-VU fixtures (only the representation this mode needs is retained).
const fixture = JSON.parse(open(`./fixtures/payloads-${SIZE}.json`));
const items = fixture.items.map((it) =>
  MODE === 'plain'
    ? { req: it.req, expect: it.resp }
    : { req: encoding.b64decode(it.req_b64, 'std'), expect: it.resp_b64 },
);
const keyText = MODE === 'aead' ? open('/run/secrets/aead_keys') : null;
let keysPromise = null;
let aadC2S;
let aadS2C;

const clientEnc = new Trend('client_encrypt_ms', true);
const clientDec = new Trend('client_decrypt_ms', true);
const sDec = new Trend('server_dec_ms', true);
const sWork = new Trend('server_work_ms', true);
const sEnc = new Trend('server_enc_ms', true);
const sApp = new Trend('server_app_ms', true);
const integrity = new Rate('integrity_ok');

const M = '{phase:measure}';
const trendT = ['max>=0'];
const thresholds = {
  [`http_req_duration${M}`]: trendT,
  [`http_req_waiting${M}`]: trendT,
  [`iteration_duration${M}`]: trendT,
  [`http_reqs${M}`]: ['count>=0'],
  [`iterations${M}`]: ['count>=0'],
  [`data_sent${M}`]: ['count>=0'],
  [`data_received${M}`]: ['count>=0'],
  [`client_encrypt_ms${M}`]: trendT,
  [`client_decrypt_ms${M}`]: trendT,
  [`server_dec_ms${M}`]: trendT,
  [`server_work_ms${M}`]: trendT,
  [`server_enc_ms${M}`]: trendT,
  [`server_app_ms${M}`]: trendT,
  [`http_req_failed${M}`]: SMOKE ? ['rate==0'] : ['rate>=0'],
  [`checks${M}`]: SMOKE ? ['rate==1'] : ['rate>=0'],
  [`integrity_ok${M}`]: SMOKE ? ['rate==1'] : ['rate>=0'],
  // Not phase-tagged by k6 (emitted by the executor, not a VU), so this covers the whole run.
  // In the open-loop experiment any dropped iteration invalidates the run (k6 exit 99).
  dropped_iterations: OPEN || SMOKE ? ['count==0'] : ['count>=0'],
};

const scenario = OPEN
  ? {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: `${WARMUP_S + DURATION_S}s`,
      preAllocatedVUs: VUS,
      maxVUs: VUS, // no mid-run VU allocation
      gracefulStop: '10s',
    }
  : { executor: 'constant-vus', vus: VUS, duration: `${WARMUP_S + DURATION_S}s`, gracefulStop: '10s' };

export const options = {
  scenarios: { bench: scenario },
  thresholds,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)', 'count'],
  systemTags: ['status', 'method', 'scenario', 'tls_version', 'error', 'error_code', 'expected_response'],
  minVersion: 'tls1.3',
  insecureSkipTLSVerify: false,
  discardResponseBodies: false,
};

const ST_RE = /dec;dur=([\d.]+), work;dur=([\d.]+), enc;dur=([\d.]+), app;dur=([\d.]+)/;

// Wall-clock start of the scenario (container clock = Docker VM clock, same as the sampler).
export function setup() {
  return { t0_epoch_ms: Date.now() };
}

// Monotonic, sub-microsecond clock (ms since test start).
const now = () => exec.instance.currentTestRunDuration;

export default async function () {
  const tStart = now();
  const measure = tStart >= WARMUP_MS;
  exec.vu.metrics.tags.phase = measure ? 'measure' : 'warmup';
  const iter = exec.scenario.iterationInTest;
  let encMs = '';
  let decMs = '';

  const item = items[exec.vu.iterationInScenario % items.length];
  let body = item.req;

  if (MODE === 'aead') {
    if (keysPromise === null) keysPromise = importKeys(keyText);
    const keys = await keysPromise;
    if (!aadC2S) {
      aadC2S = aad('c2s', 'POST', PATH, keys.kid);
      aadS2C = aad('s2c', 'POST', PATH, keys.kid);
    }
    const t0 = now();
    body = await seal(keys, item.req, aadC2S);
    encMs = now() - t0;
    clientEnc.add(encMs);
  }

  const res = http.post(URL, body, { headers: { 'Content-Type': 'application/json' } });

  let ok = false;
  if (res.status === 200) {
    if (MODE === 'aead') {
      const keys = await keysPromise;
      const t0 = now();
      try {
        ok = (await openToB64(keys, res.body, aadS2C)) === item.expect;
      } catch (_) {
        ok = false;
      }
      decMs = now() - t0;
      clientDec.add(decMs);
    } else {
      ok = res.body === item.expect;
    }
  }
  integrity.add(ok);

  const st = ST_RE.exec(res.headers['Server-Timing'] || '');
  if (st) {
    sDec.add(parseFloat(st[1]));
    sWork.add(parseFloat(st[2]));
    sEnc.add(parseFloat(st[3]));
    sApp.add(parseFloat(st[4]));
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'tls 1.3': (r) => r.tls_version === 'tls1.3',
    'server-timing present': () => st !== null,
    'output integrity': () => ok,
  });

  if (SAMPLE_EVERY > 0 && measure && iter % SAMPLE_EVERY === 0) {
    const t = res.timings;
    const f = (x) => (x === '' || x === undefined ? '' : Number(x).toFixed(4));
    // Field order must match SAMPLE_FIELDS in scripts/run_matrix.py.
    console.log(
      ['S', iter, exec.vu.idInTest, f(tStart), res.status, ok ? 1 : 0, f(t.duration), f(t.waiting),
        f(t.sending), f(t.receiving), f(t.tls_handshaking), f(encMs), f(decMs),
        st ? st[1] : '', st ? st[2] : '', st ? st[3] : '', st ? st[4] : '', f(now() - tStart)].join(','),
    );
  }

  if (exec.vu.idInTest === 1 && exec.vu.iterationInScenario === 0) {
    console.log(`negotiated tls_version=${res.tls_version} cipher=${res.tls_cipher_suite}`);
  }
}

export function handleSummary(data) {
  const out = {};
  out[`${OUT_DIR}/${RUN_ID}.summary.json`] = JSON.stringify(
    {
      run_id: RUN_ID, mode: MODE, size: SIZE, vus: VUS, warmup_s: WARMUP_S, duration_s: DURATION_S,
      executor: EXECUTOR, rate: OPEN ? RATE : null, sample_every: SAMPLE_EVERY,
      t0_epoch_ms: (data.setup_data || {}).t0_epoch_ms ?? null, k6: data,
    },
    null,
    1,
  );
  const m = (k) => data.metrics[k] && data.metrics[k].values;
  const d = m(`http_req_duration${M}`) || {};
  const n = (m(`http_reqs${M}`) || {}).count || 0;
  const dropped = (m('dropped_iterations') || {}).count ?? 0;
  out.stdout =
    `[${RUN_ID}] measured_reqs=${n} rps=${(n / DURATION_S).toFixed(1)} ` +
    `p50=${(d.med || 0).toFixed(3)}ms p95=${(d['p(95)'] || 0).toFixed(3)}ms p99=${(d['p(99)'] || 0).toFixed(3)}ms ` +
    `checks=${((m(`checks${M}`) || {}).rate ?? 'n/a')} integrity=${((m(`integrity_ok${M}`) || {}).rate ?? 'n/a')} ` +
    `dropped=${dropped}\n`;
  return out;
}
