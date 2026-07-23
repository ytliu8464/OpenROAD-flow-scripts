#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Restrict an electrical+HMAC-filtered E_C CSV to the *channel(s)* the
embedder actually drew its marks from on this design.

The channel of an LCB pair is (channel(L_A), channel(L_B)) where each LCB is
classified by ``cc.classify_lcbs`` into ``pure`` (drives only buffers) or
``quasi_leaf`` (drives sequential sinks).  A pair therefore belongs to one of
three channels:
  - pure_pure
  - quasi_quasi
  - cross  (pure x quasi_leaf, in either order)

Reads (env):
  WM_ODB         the watermarked 4_cts_wm.odb
  WM_FEAT_IN     filtered feature CSV (object_id = "L_A+L_B", label, ...)
  WM_FEAT_OUT    output channel-restricted CSV
  WM_CTS_RMAX    optional, default 5

For each pair in WM_FEAT_IN:
  1. Parse L_A, L_B from the object_id.
  2. Look up each LCB's channel.
  3. Tag the pair with its channel.

After tagging, identify the set of channels that contain at least one
positive (label==1).  Keep only the rows whose channel is in that set;
drop the rest.

Output: WM_FEAT_OUT has the same schema as WM_FEAT_IN -- no extra columns,
because downstream classifiers do not need to know which channel a row
belongs to (the restriction is *prior* to classification).
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from collections import Counter

import openroad as ord_

_HERE = Path(__file__).resolve().parent
_WM_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_WM_ROOT / "cts_v2"))
import cts_watermark_common as cc  # type: ignore


def _channel_of(name: str, pure_set: set, quasi_set: set) -> str:
    if name in pure_set:
        return "pure"
    if name in quasi_set:
        return "quasi"
    return "other"


def _pair_channel(name_a: str, name_b: str, pure_set: set, quasi_set: set) -> str:
    ca = _channel_of(name_a, pure_set, quasi_set)
    cb = _channel_of(name_b, pure_set, quasi_set)
    if ca == "pure" and cb == "pure":
        return "pure_pure"
    if ca == "quasi" and cb == "quasi":
        return "quasi_quasi"
    if {ca, cb} == {"pure", "quasi"}:
        return "cross"
    return "other"


def main() -> int:
    in_odb = os.environ["WM_ODB"]
    feat_in = Path(os.environ["WM_FEAT_IN"])
    feat_out = Path(os.environ["WM_FEAT_OUT"])
    r_max = int(os.environ.get("WM_CTS_RMAX", "5"))

    tech = ord_.Tech()
    design = ord_.Design(tech)
    design.readDb(in_odb)
    block = design.getBlock()

    classified = cc.classify_lcbs(block, r_max=r_max)
    pure_set = {c.getName() for c in classified["pure"]}
    quasi_set = {c.getName() for c in classified["quasi_leaf"]}
    sys.stderr.write(
        f"[restrict_chan] pure={len(pure_set)} quasi_leaf={len(quasi_set)}\n")

    # First pass: tag every row's channel, count channel populations, and
    # discover the channel set the embedder actually used.
    rows: list[tuple[list[str], str]] = []
    chan_counts: Counter = Counter()
    pos_chan_counts: Counter = Counter()

    with open(feat_in) as fin:
        reader = csv.reader(fin)
        header = next(reader)
        for row in reader:
            pair_key = row[0]
            try:
                label = int(row[1])
            except ValueError:
                label = 0
            parts = pair_key.split("+")
            if len(parts) != 2:
                chan = "other"
            else:
                chan = _pair_channel(parts[0], parts[1], pure_set, quasi_set)
            rows.append((row, chan))
            chan_counts[chan] += 1
            if label == 1:
                pos_chan_counts[chan] += 1

    sys.stderr.write(
        f"[restrict_chan] channel populations: " +
        ", ".join(f"{c}={n}" for c, n in chan_counts.most_common()) + "\n")
    sys.stderr.write(
        f"[restrict_chan] positives per channel: " +
        ", ".join(f"{c}={n}" for c, n in pos_chan_counts.most_common()) + "\n")

    # The embedder's actual channel set on this design.  We keep every channel
    # that contains at least one positive.  On most designs this is one
    # channel; on a few it may be two (e.g., quasi_quasi + cross).
    kept_channels = {c for c, n in pos_chan_counts.items() if n > 0}
    if not kept_channels:
        sys.stderr.write("[restrict_chan] WARNING: no positives -> keeping all rows\n")
        kept_channels = set(chan_counts.keys())

    sys.stderr.write(
        f"[restrict_chan] keeping channels: {sorted(kept_channels)}\n")

    # Second pass: write the restricted CSV.
    n_kept = 0
    n_pos_kept = 0
    feat_out.parent.mkdir(parents=True, exist_ok=True)
    with open(feat_out, "w", newline="") as fout:
        wr = csv.writer(fout)
        wr.writerow(header)
        for row, chan in rows:
            if chan not in kept_channels:
                continue
            wr.writerow(row)
            n_kept += 1
            try:
                if int(row[1]) == 1:
                    n_pos_kept += 1
            except ValueError:
                pass

    total = sum(chan_counts.values())
    total_pos = sum(pos_chan_counts.values())
    sys.stderr.write(
        f"[restrict_chan] kept {n_kept}/{total} rows ({n_pos_kept}/{total_pos} positives) "
        f"-> {feat_out}\n")
    return 0


sys.exit(main())
