# SPDX-License-Identifier: BSD-3-Clause
# Adaptive PDMarks parameter defaults for experiment drivers.
#
# Source after _common.sh.  The functions below only set variables that the
# caller has not already exported, so command-line overrides keep priority:
#   WM_CTS_NUM_PAIRS=16 DESIGN=aes ... bash drivers/run_c_only.sh

_wm_default() {
  local name="$1"
  local value="$2"
  if [[ -z "${!name:-}" ]]; then
    export "${name}=${value}"
  fi
}

_wm_clamp_int() {
  local value="$1"
  local lo="$2"
  local hi="$3"
  if (( value < lo )); then
    echo "${lo}"
  elif (( value > hi )); then
    echo "${hi}"
  else
    echo "${value}"
  fi
}

_wm_ref_info() {
  python3.11 - "${FLOW_LOG}/6_report.json" "${FLOW_RES}/clock_period.txt" <<'PY'
import json
import sys
from pathlib import Path

report = Path(sys.argv[1])
clock = Path(sys.argv[2])
d = {}
if report.exists():
    try:
        d = json.loads(report.read_text())
    except Exception:
        d = {}

tcp = 0.0
if clock.exists():
    try:
        tcp = float(clock.read_text().split()[0])
    except Exception:
        tcp = 0.0

wns = float(d.get("finish__timing__setup__ws", 0.0) or 0.0)
tns = float(d.get("finish__timing__setup__tns", 0.0) or 0.0)
stdcells = int(float(d.get("finish__design__instance__count__stdcell", 0) or 0))
nets = int(float(d.get("finish__design__nets", 0) or 0))
inst_area = float(d.get("finish__design__instance__area", 0.0) or 0.0)
core_area = float(d.get("finish__design__core__area", 0.0) or 0.0)
util = inst_area / core_area if core_area > 0 else 0.0

if tcp <= 0:
    timing_class = "unknown"
elif wns < 0:
    # All tuned references are expected to have slightly negative WNS/TNS.
    # Classify by normalized WNS instead of treating every negative run as
    # fragile, otherwise some designs receive almost no watermark capacity.
    miss = abs(wns) / tcp
    if miss <= 0.02:
        timing_class = "near_closed"
    elif miss <= 0.08:
        timing_class = "moderate_neg"
    elif miss <= 0.20:
        timing_class = "stressed"
    else:
        timing_class = "fragile"
elif wns < 0.03 * tcp:
    timing_class = "near_closed"
else:
    timing_class = "relaxed"

print(f"REF_TCP={tcp}")
print(f"REF_WNS={wns}")
print(f"REF_TNS={tns}")
print(f"REF_STDCELLS={stdcells}")
print(f"REF_NETS={nets}")
print(f"REF_UTIL={util:.6f}")
print(f"REF_TIMING_CLASS={timing_class}")
print(f"REF_WNS_FRAC={(abs(wns) / tcp) if tcp > 0 else 0.0:.6f}")
PY
}

apply_adaptive_wm_params() {
  local stages="${1:-all}"

  if [[ "${PDMARKS_ADAPTIVE_PARAMS:-1}" == "0" ]]; then
    log "adaptive params disabled (PDMARKS_ADAPTIVE_PARAMS=0)"
    return 0
  fi

  if [[ ! -f "${FLOW_LOG}/6_report.json" ]]; then
    log "adaptive params skipped: missing ${FLOW_LOG}/6_report.json"
    return 0
  fi

  eval "$(_wm_ref_info)"

  local place_target
  local cts_target
  local route_target
  local route_fraction_floor
  place_target=$(( (REF_STDCELLS + 499) / 500 ))
  route_target=$(( (REF_NETS + 99) / 100 ))

  case "${REF_TIMING_CLASS}" in
    near_closed)
      place_target="$(_wm_clamp_int "${place_target}" 48 100)"
      cts_target=24
      route_target="$(_wm_clamp_int "${route_target}" 100 300)"
      route_fraction_floor=0.02
      ;;
    moderate_neg)
      place_target="$(_wm_clamp_int "${place_target}" 40 100)"
      cts_target=20
      route_target="$(_wm_clamp_int "${route_target}" 80 240)"
      route_fraction_floor=0.015
      ;;
    stressed)
      place_target="$(_wm_clamp_int "${place_target}" 32 96)"
      cts_target=16
      route_target="$(_wm_clamp_int "${route_target}" 60 180)"
      route_fraction_floor=0.01
      ;;
    fragile)
      place_target="$(_wm_clamp_int "${place_target}" 24 72)"
      cts_target=16
      route_target="$(_wm_clamp_int "${route_target}" 50 150)"
      route_fraction_floor=0.008
      ;;
    *)
      place_target="$(_wm_clamp_int "${place_target}" 64 100)"
      cts_target=32
      route_target="$(_wm_clamp_int "${route_target}" 150 400)"
      route_fraction_floor=0.010
      ;;
  esac

  local route_fraction="${route_fraction_floor}"
  if (( REF_NETS > 0 )); then
    route_fraction="$(python3.11 - "${route_target}" "${REF_NETS}" "${route_fraction_floor}" <<'PY'
import sys
target = float(sys.argv[1])
nets = max(float(sys.argv[2]), 1.0)
floor = float(sys.argv[3])
print(f"{max(target / nets, floor):.6f}")
PY
)"
  fi

  local grid_nx grid_ny
  local pair_dist_um max_disp_um
  local hpwl_eps hpwl_eps_relaxed
  local cts_sibling_um cts_delta_sites
  if [[ "${PLATFORM}" == "asap7" ]]; then
    # ASAP7 cells and tracks are much smaller; keep micron movements modest,
    # but still widen the candidate window enough to avoid zero embedding.
    grid_nx=10
    grid_ny=10
    hpwl_eps=300
    hpwl_eps_relaxed=700
    case "${REF_TIMING_CLASS}" in
      near_closed)
        pair_dist_um=0.80; max_disp_um=0.80; cts_sibling_um=14; cts_delta_sites=8 ;;
      moderate_neg)
        pair_dist_um=0.65; max_disp_um=0.70; cts_sibling_um=12; cts_delta_sites=7 ;;
      stressed)
        pair_dist_um=0.55; max_disp_um=0.60; cts_sibling_um=10; cts_delta_sites=6 ;;
      fragile)
        pair_dist_um=0.45; max_disp_um=0.50; cts_sibling_um=8;  cts_delta_sites=5 ;;
      *)
        pair_dist_um=1.00; max_disp_um=1.00; cts_sibling_um=18; cts_delta_sites=10 ;;
    esac
  else
    # Nangate45 has larger sites/wires, so the same number of candidate sites
    # corresponds to a larger micron distance.
    grid_nx=8
    grid_ny=8
    hpwl_eps=700
    hpwl_eps_relaxed=1400
    case "${REF_TIMING_CLASS}" in
      near_closed)
        pair_dist_um=4.0; max_disp_um=4.0; cts_sibling_um=70; cts_delta_sites=18 ;;
      moderate_neg)
        pair_dist_um=3.2; max_disp_um=3.5; cts_sibling_um=55; cts_delta_sites=14 ;;
      stressed)
        pair_dist_um=2.6; max_disp_um=3.0; cts_sibling_um=45; cts_delta_sites=10 ;;
      fragile)
        pair_dist_um=2.0; max_disp_um=2.5; cts_sibling_um=35; cts_delta_sites=8 ;;
      *)
        pair_dist_um=5.0; max_disp_um=5.0; cts_sibling_um=90; cts_delta_sites=22 ;;
    esac
  fi

  case ",${stages}," in
    *,place,*|*,all,*)
      _wm_default WM_USE_GROUPS 0
      _wm_default WM_PAIRS_PER_TILE 2
      _wm_default WM_MIN_PAIRS_TOTAL "${place_target}"
      _wm_default WM_POST_GUARD 1
      _wm_default WM_POST_GUARD_FINAL_CHECK 1
      _wm_default WM_HPWL_CACHE 1
      _wm_default WM_GRID_NX "${grid_nx}"
      _wm_default WM_GRID_NY "${grid_ny}"
      _wm_default WM_PAIR_DIST_UM "${pair_dist_um}"
      _wm_default WM_MAX_DISP_X "${max_disp_um}"
      _wm_default WM_MAX_DISP_Y "${max_disp_um}"
      _wm_default WM_HPWL_EPS_PAIR_DBU "${hpwl_eps}"
      _wm_default WM_HPWL_EPS_PAIR_RELAXED_DBU "${hpwl_eps_relaxed}"

      case "${REF_TIMING_CLASS}" in
        near_closed)
          _wm_default WM_SLACK_THRESHOLD_NS 0.02
          _wm_default WM_NEIGHBOR_SLACK_MARGIN_NS 0.00
          _wm_default WM_PAIR_NEIGHBOR_K 8
          _wm_default WM_PAIR_NEIGHBOR_K_RELAXED 16
          _wm_default WM_TILE_OVERSAMPLE 8
          _wm_default WM_TILE_TOUCH_FRAC_MAX 0.05
          _wm_default WM_TILE_TOUCH_FLOOR_PAIRS 6
          _wm_default WM_GUARD_DEGRADE_NS 0.008
          ;;
        moderate_neg)
          _wm_default WM_SLACK_THRESHOLD_NS 0.01
          _wm_default WM_NEIGHBOR_SLACK_MARGIN_NS 0.00
          _wm_default WM_PAIR_NEIGHBOR_K 8
          _wm_default WM_PAIR_NEIGHBOR_K_RELAXED 16
          _wm_default WM_TILE_OVERSAMPLE 8
          _wm_default WM_TILE_TOUCH_FRAC_MAX 0.045
          _wm_default WM_TILE_TOUCH_FLOOR_PAIRS 6
          _wm_default WM_GUARD_DEGRADE_NS 0.006
          ;;
        stressed)
          _wm_default WM_SLACK_THRESHOLD_NS 0.00
          _wm_default WM_NEIGHBOR_SLACK_MARGIN_NS 0.00
          _wm_default WM_PAIR_NEIGHBOR_K 6
          _wm_default WM_PAIR_NEIGHBOR_K_RELAXED 14
          _wm_default WM_TILE_OVERSAMPLE 8
          _wm_default WM_TILE_TOUCH_FRAC_MAX 0.04
          _wm_default WM_TILE_TOUCH_FLOOR_PAIRS 5
          _wm_default WM_GUARD_DEGRADE_NS 0.005
          ;;
        fragile)
          _wm_default WM_SLACK_THRESHOLD_NS -0.02
          _wm_default WM_NEIGHBOR_SLACK_MARGIN_NS 0.00
          _wm_default WM_PAIR_NEIGHBOR_K 6
          _wm_default WM_PAIR_NEIGHBOR_K_RELAXED 12
          _wm_default WM_TILE_OVERSAMPLE 8
          _wm_default WM_TILE_TOUCH_FRAC_MAX 0.035
          _wm_default WM_TILE_TOUCH_FLOOR_PAIRS 5
          _wm_default WM_GUARD_DEGRADE_NS 0.005
          ;;
        *)
          _wm_default WM_SLACK_THRESHOLD_NS 0.10
          _wm_default WM_NEIGHBOR_SLACK_MARGIN_NS 0.05
          _wm_default WM_PAIR_NEIGHBOR_K 8
          _wm_default WM_PAIR_NEIGHBOR_K_RELAXED 18
          _wm_default WM_TILE_OVERSAMPLE 8
          _wm_default WM_TILE_TOUCH_FRAC_MAX 0.06
          _wm_default WM_TILE_TOUCH_FLOOR_PAIRS 8
          _wm_default WM_GUARD_DEGRADE_NS 0.012
          ;;
      esac
      log "adaptive place: class=${REF_TIMING_CLASS} wns_frac=${REF_WNS_FRAC} target_pairs=${WM_MIN_PAIRS_TOTAL} pair_dist=${WM_PAIR_DIST_UM}um max_disp=${WM_MAX_DISP_X}um hpwl=${WM_HPWL_EPS_PAIR_DBU}DBU guard=${WM_GUARD_DEGRADE_NS}ns"
      ;;
  esac

  case ",${stages}," in
    *,cts,*|*,all,*)
      _wm_default WM_CTS_NUM_PAIRS "${cts_target}"
      _wm_default WM_CTS_MAX_FANOUT 32
      _wm_default WM_CTS_AVOID_HOLD_REPAIR 1

      case "${REF_TIMING_CLASS}" in
        near_closed)
          _wm_default WM_CTS_SIBLING_DIST_UM "${cts_sibling_um}"
          _wm_default WM_CTS_DELTA_SITES "${cts_delta_sites}"
          _wm_default WM_CTS_R_MAX 5
          _wm_default WM_CTS_SKEW_SLACK_PS 15
          _wm_default WM_CTS_QL_SETUP_SLACK_PS 20
          _wm_default WM_CTS_QL_HOLD_SLACK_PS 25
          _wm_default WM_CTS_SLEW_HEADROOM_FRAC 0.18
          _wm_default WM_CTS_QL_CAP_HEADROOM_FRAC 0.18
          _wm_default WM_CTS_MAX_ATTEMPTS 5
          _wm_default WM_CTS_CHANNEL_BUDGET auto
          ;;
        moderate_neg)
          _wm_default WM_CTS_SIBLING_DIST_UM "${cts_sibling_um}"
          _wm_default WM_CTS_DELTA_SITES "${cts_delta_sites}"
          _wm_default WM_CTS_R_MAX 4
          _wm_default WM_CTS_SKEW_SLACK_PS 12
          _wm_default WM_CTS_QL_SETUP_SLACK_PS 15
          _wm_default WM_CTS_QL_HOLD_SLACK_PS 25
          _wm_default WM_CTS_SLEW_HEADROOM_FRAC 0.20
          _wm_default WM_CTS_QL_CAP_HEADROOM_FRAC 0.20
          _wm_default WM_CTS_MAX_ATTEMPTS 5
          _wm_default WM_CTS_CHANNEL_BUDGET auto
          ;;
        stressed)
          _wm_default WM_CTS_SIBLING_DIST_UM "${cts_sibling_um}"
          _wm_default WM_CTS_DELTA_SITES "${cts_delta_sites}"
          _wm_default WM_CTS_R_MAX 3
          _wm_default WM_CTS_SKEW_SLACK_PS 10
          _wm_default WM_CTS_QL_SETUP_SLACK_PS 10
          _wm_default WM_CTS_QL_HOLD_SLACK_PS 25
          _wm_default WM_CTS_SLEW_HEADROOM_FRAC 0.22
          _wm_default WM_CTS_QL_CAP_HEADROOM_FRAC 0.22
          _wm_default WM_CTS_MAX_ATTEMPTS 4
          _wm_default WM_CTS_CHANNEL_BUDGET auto
          ;;
        fragile)
          _wm_default WM_CTS_SIBLING_DIST_UM "${cts_sibling_um}"
          _wm_default WM_CTS_DELTA_SITES "${cts_delta_sites}"
          _wm_default WM_CTS_R_MAX 3
          _wm_default WM_CTS_SKEW_SLACK_PS 8
          _wm_default WM_CTS_QL_SETUP_SLACK_PS 8
          _wm_default WM_CTS_QL_HOLD_SLACK_PS 20
          _wm_default WM_CTS_SLEW_HEADROOM_FRAC 0.25
          _wm_default WM_CTS_QL_CAP_HEADROOM_FRAC 0.25
          _wm_default WM_CTS_MAX_ATTEMPTS 4
          _wm_default WM_CTS_CHANNEL_BUDGET auto
          ;;
        *)
          _wm_default WM_CTS_SIBLING_DIST_UM "${cts_sibling_um}"
          _wm_default WM_CTS_DELTA_SITES "${cts_delta_sites}"
          _wm_default WM_CTS_R_MAX 5
          _wm_default WM_CTS_SKEW_SLACK_PS 20
          _wm_default WM_CTS_QL_SETUP_SLACK_PS 50
          _wm_default WM_CTS_QL_HOLD_SLACK_PS 30
          _wm_default WM_CTS_SLEW_HEADROOM_FRAC 0.20
          _wm_default WM_CTS_QL_CAP_HEADROOM_FRAC 0.20
          _wm_default WM_CTS_MAX_ATTEMPTS 5
          _wm_default WM_CTS_CHANNEL_BUDGET auto
          ;;
      esac
      log "adaptive cts: class=${REF_TIMING_CLASS} pairs=${WM_CTS_NUM_PAIRS} dist=${WM_CTS_SIBLING_DIST_UM}um delta=${WM_CTS_DELTA_SITES}sites channel=${WM_CTS_CHANNEL_BUDGET}"
      ;;
  esac

  case ",${stages}," in
    *,route,*|*,all,*)
      _wm_default WATERMARK_FRACTION "${route_fraction}"
      case "${REF_TIMING_CLASS}" in
        near_closed)
          _wm_default WATERMARK_STRENGTH 80
          ;;
        moderate_neg)
          _wm_default WATERMARK_STRENGTH 70
          ;;
        stressed)
          _wm_default WATERMARK_STRENGTH 50
          ;;
        fragile)
          _wm_default WATERMARK_STRENGTH 40
          ;;
        *)
          _wm_default WATERMARK_STRENGTH 100
          ;;
      esac
      _wm_default WATERMARK_P 0.4
      log "adaptive route: class=${REF_TIMING_CLASS} target_nets=${route_target} fraction=${WATERMARK_FRACTION} strength=${WATERMARK_STRENGTH}"
      ;;
  esac
}
