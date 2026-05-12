# Runs once before global_route (and again before detail_route if both
# hooks are set). set_routing_watermark is idempotent; the dbBoolProperty
# tags persist across stages in the ODB, and the DRT picks them up at
# mazeNetInit. set_routing_watermark_strength only affects DRT.
#
# Kerckhoffs-compliant variant: when WM_SEED_HEX points at a 32-byte
# routing stage seed (e.g. seed_routing.hex from flow/watermarking/gen_key/),
# we pass it via -key_hex so the C++ side selects nets using HMAC-SHA256
# (paper Eq. eq:routing_selection).  When WM_SEED_HEX is unset we fall
# back to the legacy public -message path so older flows keep working.

set wm_fraction [env_var_or_empty WATERMARK_FRACTION]
if { $wm_fraction eq "" } { set wm_fraction 0.05 }

set wm_strength [env_var_or_empty WATERMARK_STRENGTH]
if { $wm_strength ne "" } {
  set_routing_watermark_strength $wm_strength
}

set seed_path [env_var_or_empty WM_SEED_HEX]
set used_key 0
if { $seed_path ne "" && [file exists $seed_path] } {
  set fp [open $seed_path r]
  set seed_hex [string trim [read $fp]]
  close $fp
  # Strip whitespace/newlines.
  regsub -all {[^0-9a-fA-F]} $seed_hex "" seed_hex
  if { [string length $seed_hex] == 64 } {
    set_routing_watermark -key_hex $seed_hex -fraction $wm_fraction
    set used_key 1
  } else {
    puts "[WARN] WM_SEED_HEX exists but is not 64 hex chars (got\
          [string length $seed_hex]); falling back to -message."
  }
}

if { !$used_key } {
  set wm_message [env_var_or_empty WATERMARK_MESSAGE]
  if { $wm_message eq "" } {
    puts "WATERMARK_MESSAGE not set and WM_SEED_HEX missing/invalid;\
          skipping watermark tagging."
    return
  }
  set_routing_watermark -message $wm_message -fraction $wm_fraction
}

set db    [ord::get_db]
set block [[$db getChip] getBlock]
set f [open "$::env(RESULTS_DIR)/watermark_nets.txt" w]
foreach net [$block getNets] {
  if { [odb::dbBoolProperty_find $net "watermark"] ne "NULL" } {
    puts $f [$net getName]
  }
}
close $f
