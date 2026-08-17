# SPDX-License-Identifier: BSD-3-Clause
set wm_p [env_var_or_empty WATERMARK_P]
if { $wm_p eq "" } { set wm_p 0.4 }
report_routing_watermark -p $wm_p

# Drop the tags now that they have been measured.  They name the marked nets
# in plaintext, and the routing stage rests on an observer not being able to
# tell which nets carry marks without the key -- so leaving them in the
# database would hand that set to anyone the design is shipped to.  This runs
# before the stage writes 5_2_route.odb, so every later stage inherits a
# database with no tags in it.
#
# Verification does not need them.  The routing stage is key-recoverable: the
# marked set is derived again from the key, never read back from the design.
puts "cleared [clear_routing_watermark] routing watermark tag(s)"