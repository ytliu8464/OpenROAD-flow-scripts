# Runs once before global_route (and again before detail_route if both
# hooks are set). set_routing_watermark is idempotent; the dbBoolProperty
# tags persist across stages in the ODB, and the DRT picks them up at
# mazeNetInit. set_routing_watermark_strength only affects DRT.

set wm_message  [env_var_or_empty WATERMARK_MESSAGE]
if { $wm_message eq "" } {
  puts "WATERMARK_MESSAGE not set; skipping watermark tagging."
  return
}

set wm_fraction [env_var_or_empty WATERMARK_FRACTION]
if { $wm_fraction eq "" } { set wm_fraction 0.05 }

set wm_strength [env_var_or_empty WATERMARK_STRENGTH]
if { $wm_strength ne "" } {
  set_routing_watermark_strength $wm_strength
}

set_routing_watermark -message $wm_message -fraction $wm_fraction

set db    [ord::get_db]
set block [[$db getChip] getBlock]
set f [open "$::env(RESULTS_DIR)/watermark_nets.txt" w]
foreach net [$block getNets] {
  if { [odb::dbBoolProperty_find $net "watermark"] ne "NULL" } {
    puts $f [$net getName]
  }
}
close $f