// Experiment C (H5, LLM inference): one VU alternates requests between api-plain and api-aead.
//
// Env: FIRST=plain|aead (mode "A" of the cycle)  MAX_TOKENS  WARMUP_N  MEASURE_N  RUN_ID  OUT_DIR
//
// Order (research-design.md §7.2): request i uses CYCLE[i % 8], where each entry is
// [prompt index, slot]; slot A is FIRST and slot B the other mode. Each mode receives each
// prompt equally often, and no request follows a request with the same prompt (Ollama's
// prompt cache would otherwise skip prompt processing for a repeated prompt).
// The first WARMUP_N requests (one cycle) are warm-up; the next MEASURE_N are measured.
//
// Timing sources are those of bench.js: http_req_duration and the iteration time (k6
// high-resolution timers), client encrypt/decrypt timers on the monotonic test clock, and
// the server's Server-Timing header (dec, infer, enc, app). Ollama's token counts and
// durations come from the response body (sealed in aead mode).
//
// Every measured request is written to the log as one `I,` line, so the run's request log is
// complete (no sampling). The generated text is never logged: only a SHA-256 prefix of it.
import http from 'k6/http';
import exec from 'k6/execution';
import crypto from 'k6/crypto';
import { check } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { importKeys, seal, openBytes, aad } from './lib/envelope.js';

const FIRST = __ENV.FIRST || 'plain';
const MAX_TOKENS = parseInt(__ENV.MAX_TOKENS || '32', 10);
const WARMUP_N = parseInt(__ENV.WARMUP_N || '8', 10);
const MEASURE_N = parseInt(__ENV.MEASURE_N || '16', 10);
const RUN_ID = __ENV.RUN_ID || `infer-${MAX_TOKENS}-${FIRST}`;
const OUT_DIR = __ENV.OUT_DIR || '/results/adhoc';
if (!['plain', 'aead'].includes(FIRST)) throw new Error('FIRST must be plain|aead');
if (WARMUP_N % 8 !== 0 || MEASURE_N % 8 !== 0) throw new Error('WARMUP_N, MEASURE_N: multiples of 8');

const PATH = '/v1/infer';
const URLS = { plain: `https://api-plain:8443${PATH}`, aead: `https://api-aead:8443${PATH}` };
const SECOND = FIRST === 'plain' ? 'aead' : 'plain';
const CYCLE = [[0, 'A'], [1, 'B'], [2, 'A'], [3, 'B'], [0, 'B'], [1, 'A'], [2, 'B'], [3, 'A']];

const te = new TextEncoder();
const td = new TextDecoder();
const prompts = JSON.parse(open('./fixtures/prompts.json')).items;
// Identical plaintext bodies in both modes; aead additionally needs them as bytes (prepared
// here, outside the timed region, as bench.js does).
const bodies = prompts.map((p) => {
  const s = JSON.stringify({ id: p.id, max_tokens: MAX_TOKENS, prompt: p.prompt });
  return { id: p.id, text: s, bytes: te.encode(s).buffer };
});
const keyText = open('/run/secrets/aead_keys');
let keysPromise = null;
let aadC2S;
let aadS2C;

const clientEnc = new Trend('client_encrypt_ms', true);
const clientDec = new Trend('client_decrypt_ms', true);
const sDec = new Trend('server_dec_ms', true);
const sInfer = new Trend('server_infer_ms', true);
const sEnc = new Trend('server_enc_ms', true);
const sApp = new Trend('server_app_ms', true);
const e2e = new Trend('e2e_ms', true);
const integrity = new Rate('integrity_ok');

const trendT = ['max>=0'];
const thresholds = {};
for (const m of ['plain', 'aead']) {
  const t = `{phase:measure,mode:${m}}`;
  for (const k of ['http_req_duration', 'http_req_waiting', 'e2e_ms', 'client_encrypt_ms',
    'client_decrypt_ms', 'server_dec_ms', 'server_infer_ms', 'server_enc_ms', 'server_app_ms']) {
    thresholds[`${k}${t}`] = trendT;
  }
  thresholds[`http_reqs${t}`] = ['count>=0'];
  thresholds[`checks${t}`] = ['rate>=0'];
  thresholds[`integrity_ok${t}`] = ['rate>=0'];
}

export const options = {
  scenarios: {
    infer: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: WARMUP_N + MEASURE_N,
      maxDuration: `${(WARMUP_N + MEASURE_N) * 30}s`,
      gracefulStop: '120s',
    },
  },
  thresholds,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)', 'count'],
  systemTags: ['status', 'method', 'scenario', 'tls_version', 'error', 'error_code', 'expected_response'],
  // k6 v2.3 ignores `minVersion` (bench.js uses it; see plan.md deviation 3); `tlsVersion`
  // is the supported option and pins the client to TLS 1.3.
  tlsVersion: { min: 'tls1.3', max: 'tls1.3' },
  insecureSkipTLSVerify: false,
  discardResponseBodies: false,
};

const ST_RE = /dec;dur=([\d.]+), infer;dur=([\d.]+), enc;dur=([\d.]+), app;dur=([\d.]+)/;

export function setup() {
  return { t0_epoch_ms: Date.now() };
}

const now = () => exec.instance.currentTestRunDuration;

export default async function () {
  const tStart = now();
  const i = exec.scenario.iterationInTest;
  const [p, slot] = CYCLE[i % CYCLE.length];
  const mode = slot === 'A' ? FIRST : SECOND;
  const measure = i >= WARMUP_N;
  exec.vu.metrics.tags.phase = measure ? 'measure' : 'warmup';
  exec.vu.metrics.tags.mode = mode;
  const b = bodies[p];
  let encMs = '';
  let decMs = '';
  let body = b.text;

  if (mode === 'aead') {
    if (keysPromise === null) keysPromise = importKeys(keyText);
    const keys = await keysPromise;
    if (!aadC2S) {
      aadC2S = aad('c2s', 'POST', PATH, keys.kid);
      aadS2C = aad('s2c', 'POST', PATH, keys.kid);
    }
    const t0 = now();
    body = await seal(keys, b.bytes, aadC2S);
    encMs = now() - t0;
    clientEnc.add(encMs);
  }

  const res = http.post(URLS[mode], body, { headers: { 'Content-Type': 'application/json' } });

  let doc = null;
  if (res.status === 200) {
    let text = null;
    if (mode === 'aead') {
      const keys = await keysPromise;
      const t0 = now();
      try {
        text = td.decode(await openBytes(keys, res.body, aadS2C));
      } catch (_) {
        text = null;
      }
      decMs = now() - t0;
      clientDec.add(decMs);
    } else {
      text = res.body;
    }
    try {
      doc = text === null ? null : JSON.parse(text);
    } catch (_) {
      doc = null;
    }
  }
  const meta = (doc && doc.meta) || {};
  const ok = doc !== null && doc.id === b.id && typeof doc.response === 'string';
  integrity.add(ok);

  const st = ST_RE.exec(res.headers['Server-Timing'] || '');
  if (st) {
    sDec.add(parseFloat(st[1]));
    sInfer.add(parseFloat(st[2]));
    sEnc.add(parseFloat(st[3]));
    sApp.add(parseFloat(st[4]));
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'tls 1.3': (r) => r.tls_version === 'tls1.3',
    'server-timing present': () => st !== null,
    'response authenticated and parsed': () => ok,
    'generated to the token limit': () => meta.eval_count === MAX_TOKENS && meta.done_reason === 'length',
  });

  const iterMs = now() - tStart;
  e2e.add(iterMs);

  if (measure) {
    const t = res.timings;
    const f = (x) => (x === '' || x === undefined || x === null ? '' : Number(x).toFixed(4));
    const ns = (x) => (typeof x === 'number' ? (x / 1e6).toFixed(4) : '');
    const sha = ok ? crypto.sha256(doc.response, 'hex').slice(0, 16) : '';
    // Field order must match INFER_FIELDS in scripts/run_infer.py.
    console.log(
      ['I', i, f(tStart), mode, b.id, res.status, ok ? 1 : 0, f(t.duration), f(t.waiting),
        f(t.sending), f(t.receiving), f(t.tls_handshaking), f(encMs), f(decMs),
        st ? st[1] : '', st ? st[2] : '', st ? st[3] : '', st ? st[4] : '',
        meta.done_reason || '', meta.prompt_eval_count ?? '', ns(meta.prompt_eval_duration),
        meta.eval_count ?? '', ns(meta.eval_duration), ns(meta.load_duration), ns(meta.total_duration),
        body.length, (res.body || '').length, sha, f(iterMs)].join(','),
    );
  }

  if (i === 0) console.log(`negotiated tls_version=${res.tls_version} cipher=${res.tls_cipher_suite}`);
}

export function handleSummary(data) {
  const out = {};
  out[`${OUT_DIR}/${RUN_ID}.summary.json`] = JSON.stringify(
    {
      run_id: RUN_ID, first: FIRST, max_tokens: MAX_TOKENS, warmup_n: WARMUP_N, measure_n: MEASURE_N,
      t0_epoch_ms: (data.setup_data || {}).t0_epoch_ms ?? null, k6: data,
    },
    null,
    1,
  );
  const v = (k) => (data.metrics[k] && data.metrics[k].values) || {};
  const parts = ['plain', 'aead'].map((m) => {
    const t = `{phase:measure,mode:${m}}`;
    return `${m}: n=${v(`http_reqs${t}`).count || 0} e2e_p50=${(v(`e2e_ms${t}`).med || 0).toFixed(1)}ms ` +
      `checks=${v(`checks${t}`).rate ?? 'n/a'}`;
  });
  out.stdout = `[${RUN_ID}] ${parts.join(' | ')}\n`;
  return out;
}
