"""Experiment C order properties (research-design.md §7.2), checked against the actual code."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_infer import INFER_FIELDS, build_order  # noqa: E402


def _cycle() -> list[tuple[int, str]]:
    src = (ROOT / "k6" / "infer.js").read_text()
    body = re.search(r"const CYCLE = \[(.*?)\];", src).group(1)
    return [(int(p), s) for p, s in re.findall(r"\[(\d), '([AB])'\]", body)]


def test_cycle_balanced_and_no_repeated_prompt() -> None:
    cyc = _cycle()
    assert len(cyc) == 8
    assert Counter(cyc) == Counter({(p, s): 1 for p in range(4) for s in "AB"})
    seq = cyc * 3  # repeated across cycles
    assert all(a[0] != b[0] for a, b in zip(seq, seq[1:], strict=False))


def test_sample_fields_match_k6() -> None:
    src = (ROOT / "k6" / "infer.js").read_text()
    log = src[src.index("['I', i,") : src.index("].join(',')")]
    # 'I' tag plus one expression per field
    n = len([x for x in re.split(r",\s*(?![^()]*\))", log) if x.strip()]) - 1
    assert n == len(INFER_FIELDS)


def test_build_order_blocks_and_first_mode_balance() -> None:
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    for name in ("infer-pilot", "infer"):
        prof = cfg["profiles"][name]
        order = build_order(cfg, name, prof)
        assert len(order) == prof["reps"] * len(prof["lengths"])
        for rep in range(prof["reps"]):
            assert sorted(r["length"] for r in order if r["rep"] == rep) == sorted(prof["lengths"])
        for L in prof["lengths"]:
            firsts = [r["first"] for r in order if r["length"] == L]
            assert all(a != b for a, b in zip(firsts, firsts[1:], strict=False))  # alternates
        assert order == build_order(cfg, name, prof)  # deterministic
        assert len({r["run_id"] for r in order}) == len(order)


def test_prompts_fixture_is_reproducible(tmp_path, monkeypatch) -> None:
    import gen_prompts

    monkeypatch.setattr(gen_prompts, "OUT", tmp_path / "p.json")
    gen_prompts.main()
    items = json.loads((tmp_path / "p.json").read_text())["items"]
    assert [i["id"] for i in items] == ["p1", "p2", "p3", "p4"]
    assert all(800 <= len(i["prompt"].encode()) <= 1100 for i in items)
