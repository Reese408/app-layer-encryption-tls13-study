# Publication Figures

The two figures below are grayscale on a white background, set in Times New Roman, and each exists as an SVG (editable text) and a PNG at 600 DPI. They are generated deterministically: re-running a script on the same processed data reproduces the files byte for byte. The scripts read only files in `results/processed/`, which the analysis scripts derive from `results/raw/`.

| Figure | Files | Source data | Script |
|---|---|---|---|
| H5 inference overhead | `fig_h5_inference_overhead.svg`, `.png` | `results/processed/20261021T155308Z-infer/h5.csv` (test, primary E2E measure); `h5_runs.csv` (per-block ratios) | `fig_h5_inference_overhead.py` |
| Envelope cost breakdown | `fig_envelope_cost_breakdown.svg`, `.png` | `results/processed/20261021T162323Z-envelope-micro/breakdown.csv` | `fig_envelope_cost_breakdown.py` |

Shared style: `paper_style.py`.

## Reproduction

```
make h5 RUN=results/raw/20261021T155308Z-infer
uv run python analysis/envelope_micro.py results/raw/20261021T162323Z-envelope-micro
make figures
```

## Suggested captions and reading notes

**H5 inference overhead.** *Estimated application-layer encryption overhead during Qwen inference, with one-sided 95% upper confidence bounds relative to the pre-specified 1% threshold.*
- **What is plotted:** the overhead share Δ / T_AEAD = 1 − 1/*r*, for end-to-end latency (the pre-registered primary measure). Three things are shown:
  - **filled squares:** point estimates, from the geometric mean of the per-block ratios;
  - **brackets:** one-sided 95% upper confidence bounds, from *t* with 4 degrees of freedom. These bounds are the tested quantity: 0.358% at 32 tokens and 0.645% at 256 tokens;
  - **open circles:** the individual blocks (*n* = 5 per length).
- **Reading notes:**
  - The lower side is not bounded, because the test is one-sided.
  - A negative estimate (−0.458% at 32 tokens) means the encrypted runs were not slower on average. It does not mean encryption made requests faster: run-level differences track the model's own processing time (results-inference.md §2.3).
  - One 256-token block lies at about 1.0%. Individual blocks are not the test.

**Envelope cost breakdown.** *Relative contribution of AES-GCM, Base64, and JSON operations to server-side envelope processing time across three payload sizes.*
- **Data:** a descriptive microbenchmark in the API image (2 CPUs, no network), with 5 container processes × 25 repetitions.
- **How each share is computed:** an operation's median time is divided by the median time of the whole seal or open operation, within each process. The median of those per-process shares is plotted.
- **Residual:** the whole operation minus the sum of the timed components, shown as measured. This includes −0.6% for sealing 256 KiB. Bars are not normalised to 100%.
- **Not shown:** confidence intervals; the k6 client; network bytes; any binary-envelope alternative.
