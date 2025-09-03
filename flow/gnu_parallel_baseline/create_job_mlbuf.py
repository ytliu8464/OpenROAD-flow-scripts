import os
import shutil

fname = "joblist_test"
f = open(fname, "w")
f.close()

design_list = []
#design_list.append("ibex")
design_list.append("ariane133")
#design_list.append("jpeg")


model_list = []
model_list.append("v4__cn10_twoWayArea005_cmax15_3")
model_list.append("v4__cn20_diffArea001_cmax20_3")
model_list.append("v4__cn20_twoWayArea03_cmax15_3")
model_list.append("v4__cn30_diffArea005_cmax15_4")
model_list.append("v4__cn10_twoWayArea03_cmax10_4")
model_list.append("v4__cn20_twoWayArea005_cmax15_3")
model_list.append("v4__cn30_diffArea005_cmax15_3")
model_list.append("v4__cn10_DiffArea001_3")
model_list.append("v4__cn20_diffArea001_cmax20_3")
model_list.append("v4__cn30_DiffArea0005_3")
model_list.append("v4__cn30_DiffArea001_3")
model_list.append("v4__cn30_DiffArea001_5")
model_list.append("v4__cn30_DiffArea005_3")
model_list.append("v4__cn30_diffArea005_cmax15_3")
model_list.append("v4__cn30_diffArea005_cmax15_4")
model_list.append("v4__cn30_newArea01_3")
model_list.append("v4__cn50_newArea001_4")
model_list.append("v4__cn50_DiffArea001_3")
model_list.append("v4__cn50_DiffArea001_4")

design_full_name_map = {}
design_full_name_map["ibex"] = "ibex"
design_full_name_map["ariane133"] = "ariane"
design_full_name_map["jpeg"] = "jpeg_encoder"

path = os.getcwd() + "/../"
cur_path = os.getcwd()
print("path = ", path)

job_dir = os.getcwd() + "/job_dir"
if os.path.isdir(job_dir):
    #shutil.rmtree(job_dir)
    #os.mkdir(job_dir)
    pass
else:
    os.mkdir(job_dir)


template_sh_file = "/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow/gnu_parallel_baseline/run_ORFS_mlbuf_job.sh"
def create_sh_file(design_file, design_name, design_full_name, flow_name):
    with open(template_sh_file, "r") as f:
        lines = f.read().splitlines()
    f.close()
        
    f = open(design_file, "w")
    for line in lines:
        if "DESIGN=" in line:
            line = line.replace("xx", design_name)
        if "DESIGN_FULL_NAME" in line:
            line = line.replace("xx", str(design_full_name))
        if "FLOW_VARIANT" in line:
            line = line.replace("xx", str(flow_name))
        f.write(line + "\n")
    f.close()


pwd = os.getcwd()
for design in design_list:
    for model in model_list:
        run_sh_file = pwd + "/" +"run_mlbuf_" + design + "_" + model + ".sh"
        create_sh_file(run_sh_file, design, design_full_name_map[design], model)
        cmd = "chmod +x " + run_sh_file
        os.system(cmd)

        job_file = job_dir + "/" + design + "_" + model + ".sh"
        f = open(job_file, "w")
        f.write("#!/bin/bash\n")
        f.write("cd " + pwd + "\n")
        f.write("source " + run_sh_file + "\n")
        f.write("cd " + cur_path + "\n")
        f.close()

        cmd = "chmod +x " + job_file
        os.system(cmd)
        f = open(fname, "a")
        f.write(job_file + "\n")
        f.close()
        print("job_file = ", job_file)











