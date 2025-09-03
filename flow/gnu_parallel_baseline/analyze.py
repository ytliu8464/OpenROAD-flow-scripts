import os
import sys


# define the function for parse metrics
def parse_metrics(testcase, flow_variant):
    # define the path to the metrics file
    # metric_file = os.getcwd() + "/logs_0902_mismatch/nangate45/" + testcase + "/" + flow_variant + "/6_report.json" 
    # metric_file_wl = os.getcwd() + "/logs_0902_mismatch/nangate45/" + testcase + "/" + flow_variant + "/5_2_route.json"
    metric_file = os.getcwd() + "/logs/nangate45/" + testcase + "/" + flow_variant + "/6_report.json" 
    metric_file_wl = os.getcwd() + "/logs/nangate45/" + testcase + "/" + flow_variant + "/5_2_route.json"
    
    rWL = None
    WNS = None
    TNS = None
    Power = None

    # check if the file exists
    if not os.path.exists(metric_file):
        print(f"Metrics file not found: {metric_file}")
        return rWL, WNS, TNS, Power
   

    with open(metric_file, 'r') as f:
        lines = f.read().splitlines()
    f.close()

    # parse the metrics from the file
    for line in lines:
        if "finish__timing__setup__tns" in line:
            items = line.split(" ")
            TNS = float(items[1][:-1])
        if "finish__timing__setup__ws" in line:
            items = line.split(" ")
            WNS = float(items[1][:-1])
        if "finish__power__total" in line:
            items = line.split(" ")
            Power = float(items[1][:-1])

    with open(metric_file_wl, 'r') as f:
        lines = f.read().splitlines()
    f.close()

    # parse the metrics from the file
    for line in lines:
        if "detailedroute__route__wirelength" in line:
            items = line.split(" ")
            rWL = float(items[1][:-1])
     

    print("testcase: ", testcase, " flow_variant: ", flow_variant, 
          " Power: ", Power, " rWL: ", rWL, " WNS: ", WNS, " TNS: ", TNS)
    return rWL, WNS, TNS, Power            


# define the testcases
design_list = []
design_list.append("ibex")
#design_list.append("ariane133")
design_list.append("jpeg")

# all the different flow variants
flow_variant_list = []
flow_variant_list.append("no_timing")
flow_variant_list.append("rsz_virtual")
# flow_variant_list.append("rsz_default")
flow_variant_list.append("rsz_hacky")

model_list = []
# model_list.append("v4__cn10_twoWayArea005_cmax15_3")
# model_list.append("v4__cn10_twoWayArea03_cmax10_4")
# model_list.append("v4__cn10_DiffArea001_3")
model_list.append("v4__cn20_diffArea001_cmax20_3")

# model_list.append("v4__cn20_twoWayArea03_cmax15_3")
# model_list.append("v4__cn20_twoWayArea005_cmax15_3")
# model_list.append("v4__cn30_diffArea005_cmax15_4")
# model_list.append("v4__cn30_diffArea005_cmax15_3")

# model_list.append("v4__cn30_DiffArea0005_3")
# model_list.append("v4__cn30_DiffArea001_3")
# model_list.append("v4__cn30_DiffArea001_5")
# model_list.append("v4__cn30_DiffArea005_3")

# model_list.append("v4__cn30_newArea01_3")
# model_list.append("v4__cn50_newArea001_4")
# model_list.append("v4__cn50_DiffArea001_3")
# model_list.append("v4__cn50_DiffArea001_4")



for model in model_list:
    flow_variant_list.append(model)


summary_file = "summary_result_or_0902_test2.csv"
f = open(summary_file, 'w')
f.write("testcase,flow_variant,rWL,WNS,TNS,Power\n")
f.close()



design_full_name_map = {}
design_full_name_map["ibex"] = "ibex"
design_full_name_map["ariane133"] = "ariane"
design_full_name_map["jpeg"] = "jpeg_encoder"

for design in design_list:
    for flow_variant in flow_variant_list:
        # call the function to parse the metrics
        rWL, WNS, TNS, Power = parse_metrics(design,  flow_variant)
        # open the summary file in append mode
        f = open(summary_file, 'a')
        # write the metrics to the file
        f.write(f"{design_full_name_map[design]},{flow_variant},{rWL},{WNS},{TNS},{Power}\n")        
        # close the file
        f.close()






