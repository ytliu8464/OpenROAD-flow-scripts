# ===============================================================
#  MLBUF‑guided buffer insertion for OpenROAD
# ===============================================================
import re
from collections import defaultdict, Counter
import typing
import ast
import openroad as ord
import odb                       # OpenROAD DB python wrapper
from openroad import Timing   
import csv
from collections import defaultdict
from typing import Dict, List, Any, Optional
# ----------------------------------------------------------------------
# 0. Configuration section – modify as needed
# ----------------------------------------------------------------------
BUF_CELL_TABLE = {
    0: "None",
    1: "BUF_X2",
    2: "BUF_X4",
    3: "BUF_X8",
    4: "BUF_X16",
    5: "BUF_X32"          
}

BUF_NAME_TO_ID = {
    "-1": 0,
    "BUF_X2": 1,
    "BUF_X4": 2,
    "BUF_X8": 3,
    "BUF_X16": 4,
    "BUF_X32": 5          
}

BUF_CELL_Area = [0, 1.064, 1.862, 3.458, 6.65, 13.03]
MLBUF_FILE = "mlbuf.csv"
RSZ_FILE = "rsz.csv"  

# ----------------------------------------------------------------------
# 1. insert_buffer_ex – extended version: returns *output net name*
# driver -> new_net -> buffer -> source_net -> sinks
# ----------------------------------------------------------------------
def insert_buffer_ex_wrong(sta,
                     net_name: str,
                     buf_cell_type: str,
                     inserted_buffer_count: int,
                     lx: int = -1,
                     ly: int = -1) -> typing.Optional[str]:

    """
    Insert buffer and return output net name (string); return None on failure
    """
    db = ord.get_db()
    block = ord.get_db_block()  
    source_net = block.findNet(net_name)

    if source_net is None:
        print(f"[WARN] Net {net_name} not found.")
        return None

    # ---- Find driver pin ---------------------------------------------
    driver_pin = next((p for p in source_net.getITerms() if p.isOutputSignal()),
                      None)
    if driver_pin is None:
        driver_pin = next((p for p in source_net.getBTerms()
                           if p.getIoType() == "INPUT"), None)
    if driver_pin is None:
        print(f"[WARN] No driver pin for net {net_name}")
        return None

    # ---- POWER / GND nets -------------------------------------------
    power_net = gnd_net = None
    for pin in driver_pin.getInst().getITerms():
        if pin.getSigType() == 'POWER':
            power_net = pin.getNet()
        elif pin.getSigType() == 'GROUND':
            gnd_net = pin.getNet()
    if not power_net or not gnd_net:
        print(f"[WARN] Cannot find VDD/VSS for {net_name}")
        return None

    # ---- Buffer coordinates ------------------------------------------
    if lx < 0 or ly < 0:
        bb = driver_pin.getInst().getBBox()
        lx = (bb.xMin() + bb.xMax()) // 2
        ly = (bb.yMin() + bb.yMax()) // 2

    master = db.findMaster(buf_cell_type)
    if master is None:
        print(f"[WARN] buffer cell {buf_cell_type} not found in dbLib.")
        return None

    buf_name = f"inserted_buffer_{inserted_buffer_count}"
    new_inst = odb.dbInst_create(block, master, buf_name)
    new_inst.setOrient('R0')
    new_inst.setPlacementStatus('PLACED')
    new_inst.setLocation(lx, ly)

    # ---- Create new net for buffer OUTPUT---------------------------------------------
    new_net_name = f"{buf_name}_net"
    new_net = odb.dbNet_create(block, new_net_name)

    # Connect pins
    for iterm in new_inst.getITerms():
        if iterm.isInputSignal():
            iterm.connect(source_net)          # input ← old_net
        elif iterm.isOutputSignal():
            iterm.connect(new_net)      # output → new_net
        elif iterm.getSigType() == 'POWER':
            iterm.connect(power_net)
        elif iterm.getSigType() == 'GROUND':
            iterm.connect(gnd_net)

    print(f"[INFO] Inserted {buf_cell_type} ({buf_name}) on {net_name} "
          f"→ new net {new_net_name}")
    return new_inst, new_net

def insert_buffer_ex(block,
                     old_net: odb.dbNet,
                     buf_cell_type: str,
                     inst_name: str,
                     power_net: odb.dbNet,
                     gnd_net:   odb.dbNet,
                     lx: int,
                     ly: int):
    """
    Insert <buf_cell_type> instance on <old_net> at (lx, ly) [DBU].
    Return (buf_inst, buf_output_net)   — never returns None.
    """
    db = ord.get_db()
    block = ord.get_db_block()  
    # master = block.getDb().findMaster(buf_cell_type)
    master = db.findMaster(buf_cell_type)
    if master is None:
        raise RuntimeError(f"buffer cell {buf_cell_type} not found")

    buf_inst = odb.dbInst_create(block, master, inst_name)
    buf_inst.setOrient('R0')
    buf_inst.setLocation(lx, ly)
    buf_inst.setPlacementStatus('PLACED')

    # create new net for buffer OUTPUT
    new_net_name = f"{inst_name}_net"
    buf_out_net  = odb.dbNet_create(block, new_net_name)

    # Connect pins
    for iterm in buf_inst.getITerms():
        if iterm.isInputSignal():
            iterm.connect(old_net)          # 输入 ← old_net
        elif iterm.isOutputSignal():
            iterm.connect(buf_out_net)      # 输出 → new_net
        elif iterm.getSigType() == 'POWER':
            iterm.connect(power_net)
        elif iterm.getSigType() == 'GROUND':
            iterm.connect(gnd_net)

    return buf_inst, buf_out_net
# ----------------------------------------------------------------------
# 2. CSV Parser
# ----------------------------------------------------------------------
def _parse_node_attrs(attr_str: str) -> Dict[str, Optional[str]]:

    get = lambda pat: re.search(pat, attr_str)
    
    return {
        "type":    (m := get(r'Type:\s*([\w/]+)')) and m.group(1),
        "x":       float((m := get(r'X:\s*([-\d.]+)')) .group(1)) if m else None,
        "y":       float((m := get(r'Y:\s*([-\d.]+)')) .group(1)) if m else None,
        "buf_id":  int(  (m := get(r'Buf_id:\s*([-\d]+)')).group(1)) if m else None,
        "pin":     (m := get(r'Pin:\s*([^\s,)]+)')) and m.group(1),
    }

def parse_mlbuf(filename: str):

    nets: Dict[str, List[dict]] = {}
    current_net: Optional[str] = None

    pat_head = re.compile(r"===Predicted Buffered Tree for NET \[(.*)\]")

    pat_node = re.compile(
        r"Node id:\s*(\d+),"          # 1. id
        r"\s*Parent id:\s*(None|\d+),"# 2. parent id
        r"\s*(.*)$"                   # 3. others
    )

    with open(filename, encoding="utf-8") as fp:
        for line in fp:
            # new net
            if (mh := pat_head.search(line)):
                net_tuple = ast.literal_eval(mh.group(1))
                current_net = net_tuple[0]

                nets[current_net] = []
                continue

            if current_net is None:     
                continue

            # node
            if (mn := pat_node.search(line)):
                nid, pid, attr_str = mn.groups()
                attrs = _parse_node_attrs(attr_str)
                clean_pin_name = attrs["pin"].strip().strip("()'\"") 

                nets[current_net].append({
                    "id":        int(nid),
                    "parent":    None if pid == "None" else int(pid),
                    "type":      attrs["type"] or "Unknown",
                    "x":        attrs["x"]  if attrs["x"]  is not None else 0.0,
                    "y":        attrs["y"]  if attrs["y"]  is not None else 0.0,
                    "buf_id":   attrs["buf_id"] if attrs["buf_id"] is not None else -1,
                    "pin_name": clean_pin_name,
                })

    return nets

def _buf_area(buf_id: int) -> float:
    """Return the single‑cell area for the given buf_id (µm²)."""
    if 0 <= buf_id < len(BUF_CELL_Area):
        return BUF_CELL_Area[buf_id]
    # fall‑back to the largest entry if id is out of range
    return BUF_CELL_Area[-1]


def _norm(col: str) -> str:
    """lower‑case, strip, compress ALL whitespace to single space"""
    _WS_RE = re.compile(r"\s+")
    return _WS_RE.sub(" ", col.lower().strip())

def parse_rsz(filename: str) -> Dict[str, List[dict]]:
    """
    nets[net_name] -> list[
        {id, parent, type, x, y, buf_id, pin_name}
    ]
    """
    
    nets: Dict[str, List[dict]] = defaultdict(list)
    print(f"[INFO] Parsing {filename}...")

    with open(filename, newline="", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp, skipinitialspace=True)

        if reader.fieldnames is None:
            raise ValueError("CSV header row missing.")

        # -------- header normalization --------
        norm_cols = {_norm(h): h for h in reader.fieldnames}

        def col(name: str, *, required: bool = False) -> Optional[str]:
            key = _norm(name)
            if required and key not in norm_cols:
                raise KeyError(f"Required column “{name}” not found (after normalizing header). "
                               f"Available: {list(norm_cols)}")
            return norm_cols.get(key)

        # mandatory columns
        NET         = col("net name", required=True)
        NODE_ID     = col("node id", required=True)
        NODE_TYPE   = col("node type", required=True)
        X           = col("x", required=True)
        Y           = col("y", required=True)
        PARENT_ID   = col("parent node id", required=True)
        PIN_NAME    = col("pin name", required=True)

        # optional
        BUF_TYPE    = col("buffer type")       # may be None

        # helper: decode buffer type => int
        def buf_to_int(raw: str) -> int:
            raw = raw.strip()
            if raw in ("", "-1", "None"):
                return 0
            if raw in BUF_NAME_TO_ID:
                return BUF_NAME_TO_ID[raw]
            # if raw.lstrip("-").isdigit():
            #     return int(raw)
            # m = re.search(r"(\d+)$", raw)
            # return int(m.group(1)) if m else 0

        # -------- read rows --------
        for row in reader:
            net_name = row[NET].strip()
            if not net_name:
                continue      # skip blank net field

            nid = int(row[NODE_ID])
            node_type = row[NODE_TYPE].strip()

            parent_raw = row[PARENT_ID].strip()
            parent = None if parent_raw in ("", "-1", "None") else int(parent_raw)

            x = float(row[X])
            y = float(row[Y])

            buf_id = buf_to_int(row[BUF_TYPE]) if BUF_TYPE else 0
            pin_name = row[PIN_NAME].strip() or None

            nets[net_name].append({
                "id": nid,
                "parent": parent,
                "type": node_type,
                "x": x,
                "y": y,
                "buf_id": buf_id,
                "pin_name": pin_name
            })

    return nets

def parse_rsz_wrong(filename: str) -> Dict[str, List[dict]]:
    """
    Parse the “RSZ” CSV described in the prompt and return:
        nets[net_name] -> list[node_dict]

    node_dict fields (compatible with legacy format):
        id, parent, type, x, y, buf_id, pin_name
    """
    nets: Dict[str, List[dict]] = defaultdict(list)

    # Open CSV; skipinitialspace=True ignores spaces after commas
    with open(filename, newline='') as fp:
        reader = csv.DictReader(fp, skipinitialspace=True)

        # Normalize headers: strip and lowercase for robust access
        norm_cols = {h.strip().lower(): h for h in reader.fieldnames}

        def col(key: str) -> str:
            """Helper to get original column name; raises KeyError if missing"""
            return norm_cols[key]

        for row in reader:
            net_name: str = row[col("net name")].strip()

            # --- Required fields ---
            nid: int = int(row[col("node id")])
            node_type: str = row[col("node type")].strip()

            # --- Parent field parsing ---
            parent_raw: str = row.get(col("parent node id"), "").strip()
            parent: Optional[int] = None
            if parent_raw and parent_raw not in ("-1", "None"):
                parent = int(parent_raw)

            # --- Coordinates ---
            x: float = float(row[col("x")])
            y: float = float(row[col("y")])

            # --- Buffer Type to buf_id ---
            buf_raw: str = row.get(col("buffer type"), "").strip()
            buf_id: int = 0
            if buf_raw and buf_raw not in ("-1", "None"):
                try:
                    buf_id = int(buf_raw)
                except ValueError:
                    # If buffer type is a string like "X4" or "BUF_X4",
                    # map it here (optional); currently defaulting to 0
                    buf_id = 0

            pin_name: Optional[str] = row.get(col("pin name"), "").strip() or None

            nets[net_name].append({
                "id": nid,
                "parent": parent,
                "type": node_type,
                "x": x,
                "y": y,
                "buf_id": buf_id,
                "pin_name": pin_name
            })

    return nets

# ----------------------------------------------------------------------
# 3. Build tree & infer missing buf_ids
# ----------------------------------------------------------------------
def build_tree(nodes):
    id2node = {n["id"]: n for n in nodes}  # map id -> node
    children = defaultdict(list)
    for n in nodes:
        if n["parent"] is not None:
            children[n["parent"]].append(n["id"])
    return id2node, children

def enrich_buffer_ids(id2node, children):  # find buffer type according to buffer's children
    for n in id2node.values():
        if n["type"] != "Buffer" or n["pin_name"].find("BUF_AUTO") != -1:
            continue
        sib = [id2node[c]["buf_id"] for c in children[n["id"]]
               if id2node[c]["buf_id"] != 0]
        n["buf_id"] = Counter(sib).most_common(1)[0][0] if sib else 5

# ----------------------------------------------------------------------
# 4. Utility functions
# ----------------------------------------------------------------------
def micron2dbu(val_um: float, dbu: int) -> int:
    return int(round(val_um * dbu))

def escape_array_indices(pin_name: str) -> str:
    """
    Escape square brackets in module/instance names within a pin path.
    E.g. "foo/bar[3].baz" -> "foo/bar\\[3\\].baz"
    """
    return re.sub(r'([\[\]])', r'\\\1', pin_name)

def disconnect_and_connect_pin(block, pin_name: str, target_net, verbose=True):
    """
    pin_name format: 'hier/inst/XYZ/A'. For top-level IOs, use 'PINNAME'.
    """
    if not pin_name:
        return
    
    if "/" not in pin_name:
        bterm = block.findBTerm(pin_name)
        if bterm:
            if verbose:
                print(f"    ↪  reconnect TOP IO {pin_name} → {target_net.getName()}")
            bterm.disconnect()
            bterm.connect(target_net)
        return
    inst_path, port = pin_name.rsplit("/", 1)
    inst_path = escape_array_indices(inst_path)
    inst = block.findInst(inst_path)
    if not inst:
        if verbose:
            print(f"[WARN] inst {inst_path} not found for {pin_name}")
        return
    iterm = inst.findITerm(port)
    if not iterm:
        if verbose:
            print(f"[WARN] port {port} not found on {inst_path}")
        return
    # if verbose:
    #     print(f"    ↪  reconnect {pin_name} → {target_net.getName()}")
    iterm.disconnect()
    iterm.connect(target_net)

# ------------------------------------------------------------------
#  5. insert_from_tree  (bottom-up version)
# ------------------------------------------------------------------
def insert_from_tree(dbu,sta, design,
                     top_net_name: str,
                     id2node, children,
                     inserted_cnt, type_hist, area_accum, defined_str):
    """
    Traversal order: children → parent (true bottom-up)
    After inserting each Buffer:
        • Move its directly connected sink pins
        • And input pins of its buffer-children
      from old_net to buffer_out_net
    """
    block = design.getBlock()
    BUF_DEFAULT = BUF_CELL_TABLE[5]  # Fallback buffer cell if none is specified

    # ------ Cache: input net for each node ---------------------
    in_net_of = {}            # node_id -> odb.dbNet
    in_net_of[0] = block.findNet(top_net_name)

    # ------ Locate power/ground nets ---------------------------
    # any_inst = next(block.getInsts())  # Use any instance to identify power/ground nets
    any_inst = next(iter(block.getInsts()), None)
    if any_inst is None:
        raise RuntimeError("Design contains no instances – cannot locate VDD/VSS nets")
    pwr_net = gnd_net = None
    for it in any_inst.getITerms():
        if it.getSigType() == 'POWER':
            pwr_net = it.getNet()
        elif it.getSigType() == 'GROUND':
            gnd_net = it.getNet()
    assert pwr_net and gnd_net, "Cannot find VDD/VSS"

    # ------ Helper: get the unique input/output pin of an instance -----
    def input_iterm(inst):
        return next(p for p in inst.getITerms() if p.isInputSignal())
    
    node2inst = {}                #  <node id> -> <odb.dbInst>      
    # ------ Main recursive DFS traversal ------------------------------
    def dfs(nid: int):

        node = id2node[nid]
        
        # 1) Sink node — no action needed
        if node["type"] == "Sink":
            return

        # 2) Recurse into children first
        for cid in children.get(nid, []):
            in_net_of[cid] = in_net_of[nid]  # Inherit parent’s input net initially
            dfs(cid)

        # 3) If this node is a Buffer, insert the buffer instance
        if node["type"] != "Buffer":
            return

        old_net = in_net_of[nid]  # The net currently driving this node
        cell    = BUF_CELL_TABLE.get(node["buf_id"], BUF_DEFAULT)
        lx = micron2dbu(node["x"], dbu)
        ly = micron2dbu(node["y"], dbu)
        # lx = int(node["x"])
        # ly = int(node["y"])
        inst_name = f"{defined_str}_buf_{inserted_cnt[0]}"

        buf_inst, buf_out_net = insert_buffer_ex(
            block=block,
            old_net=old_net,
            buf_cell_type=cell,
            inst_name=inst_name,
            power_net=pwr_net,
            gnd_net=gnd_net,
            lx=lx, ly=ly
        )

        # ----- statistics: buffer area, buffer type distribution -----
        type_hist[cell] += 1
        area_accum[0] += _buf_area(node["buf_id"])
        
        node2inst[nid] = buf_inst 
        inserted_cnt[0] += 1

        # ---- 3a. Reconnect direct sink pins to buffer output net -----
        for cid in children.get(nid, []):
            cnode = id2node[cid]
            if cnode["type"] == "Sink":
                disconnect_and_connect_pin(block,
                                           cnode["pin_name"],
                                           buf_out_net)

        # ---- 3b. Reconnect input pins of direct buffer-children -------
            elif cnode["type"] == "Buffer":
                # child_inst = block.findInst(f"{defined_str}_buf_{inserted_cnt[0]}")
                child_inst = node2inst.get(cid)
                if not child_inst:
                    print(f"[WARN] buffer inst for node {cid} not found")
                    continue
                ip = input_iterm(child_inst)
                ip.disconnect()
                ip.connect(buf_out_net)
                in_net_of[cid] = buf_out_net  # Update child’s input net

        # Provide buffer output net to this buffer’s parent
        in_net_of[nid] = buf_out_net

    # ---- Start from the driver node (id = 0) ----
    dfs(0)
    
# ----------------------------------------------------------------------
# 6. Entry point: batch insert for all nets
# ----------------------------------------------------------------------
def run_mlbuf_insertion(design, sta, buffer_tree_file: str = MLBUF_FILE, rsz_postTree_file:str = RSZ_FILE, mlbuf_flag: bool = True,
                        start_cnt: int = 0) -> int:
    dbu = design.getBlock().getDbUnitsPerMicron()

    # ---------- statistics containers ----------
    inserted_cnt = [start_cnt]
    type_hist    = Counter()
    area_accum   = [0.0]            # list for mutability

    if mlbuf_flag:  # parser mlbuf results
        nets = parse_mlbuf(buffer_tree_file)

        for net_name, nodes in nets.items():
            print(f"[INFO] Inserting buffers for net {net_name}")
            # exit()
            id2node, children = build_tree(nodes) # build tree
            enrich_buffer_ids(id2node, children) # find buffer type
            insert_from_tree(dbu, sta, design, net_name, id2node, children,
                            inserted_cnt,type_hist, area_accum,"mlbuf")
        total = inserted_cnt[0] - start_cnt
        print(f"[INFO] MLBUF insertion finished: +{total} buffers")
        
    else:  # parser rsz results
        nets = parse_rsz(rsz_postTree_file)

        for net_name, nodes in nets.items():
            print(f"[INFO] Inserting buffers for net {net_name}")
            id2node, children = build_tree(nodes) # build tree
            insert_from_tree(dbu, sta, design, net_name, id2node, children,
                            inserted_cnt,type_hist, area_accum, "rsz")

        total = inserted_cnt[0] - start_cnt
        print(f"[INFO] RSZ insertion finished: +{total} buffers")
    
    return inserted_cnt[0], area_accum[0], dict(type_hist)

# ----------------------------------------------------------------------
    #      mlbuf.csv
    #         ↓
    #   parse_mlbuf() → per-net tree
    #         ↓
    # build_tree(): [map id->node, and parent and child], enrich_buffer_ids(): [find buffer type]
    #         ↓
    # insert_from_tree()  (iterativly insert buffers [top-down])
    #         ↓
    # insert_buffer_ex()  (invoke odb to create buffer + net)
    """
    driver ── net0 ── sinkA
             │
             └─ buf1 ── net1 ── sinkB
                           │
                           └─ buf2 ── net2 ── sinkC ...
    """