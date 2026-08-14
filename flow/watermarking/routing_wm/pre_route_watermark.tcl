# SPDX-License-Identifier: BSD-3-Clause
# Runs once before global_route (and again before detail_route if both
# hooks are set). set_routing_watermark is idempotent; the dbBoolProperty
# tags persist across stages in the ODB, and the DRT picks them up at
# mazeNetInit. set_routing_watermark_strength only affects DRT.
#
# WM_SEED_HEX names the 32-byte routing seed from flow/watermarking/gen_key/.
# It is passed as -key_hex so the C++ side selects nets with HMAC-SHA256
# (paper Eq. eq:routing_selection).  Selection is keyed unconditionally --
# there is no public-message fallback.

set wm_fraction [env_var_or_empty WATERMARK_FRACTION]
if { $wm_fraction eq "" } { set wm_fraction 0.05 }

set wm_strength [env_var_or_empty WATERMARK_STRENGTH]
if { $wm_strength ne "" } {
  set_routing_watermark_strength $wm_strength
}

# Net selection is keyed: WM_SEED_HEX must name a 32-byte routing seed.
# Any problem is a hard error -- silently skipping the tagging step would
# produce a clean-looking flow with no watermark in it, and falling back to a
# public message would defeat the point of keying the selection at all.
set seed_path [env_var_or_empty WM_SEED_HEX]
if { $seed_path eq "" } {
  error "pre_route_watermark: WM_SEED_HEX is not set. Point it at\
         gen_key/out/<design>/seed_routing.hex."
}
if { ![file exists $seed_path] } {
  error "pre_route_watermark: WM_SEED_HEX=$seed_path does not exist.\
         Generate it with gen_key/gen_key.sh sign."
}

set fp [open $seed_path r]
set seed_hex [string trim [read $fp]]
close $fp
regsub -all {[^0-9a-fA-F]} $seed_hex "" seed_hex
if { [string length $seed_hex] != 64 } {
  error "pre_route_watermark: $seed_path holds [string length $seed_hex] hex\
         chars, expected 64 (a 32-byte seed)."
}

set_routing_watermark -key_hex $seed_hex -fraction $wm_fraction

set db    [ord::get_db]
set block [[$db getChip] getBlock]
set f [open "$::env(RESULTS_DIR)/watermark_nets.txt" w]
foreach net [$block getNets] {
  if { [odb::dbBoolProperty_find $net "watermark"] ne "NULL" } {
    puts $f [$net getName]
  }
}
close $f
