set wm_p [env_var_or_empty WATERMARK_P]
if { $wm_p eq "" } { set wm_p 0.4 }
report_routing_watermark -p $wm_p