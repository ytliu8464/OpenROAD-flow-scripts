# MLBuf ERC Checker

## Overview
This project provides a Python script integrated with **OpenROAD** for checking **Electrical Rule Check (ERC) violations** and performing **buffer insertion** using **MLBuf** predictions or OR RSZ results.  

It supports:
- Loading technology LEF/Liberty and `.sdc` constraints.  
- Reading placed design snapshots (`.odb`).  
- Running **MLBuf-based buffer insertion** or fallback to **RSZ**.  
- Checking **slew**, **capacitance**, and **fanout** violations.  
- Logging all results to a file for reproducibility.  

---

## Features
- ✅ Integrates seamlessly with **OpenROAD Python API**.  
- ✅ Checks and reports **ERC violations** (slew, capacitance, fanout).  
- ✅ Optional **buffer insertion** based on MLBuf or RSZ CSV results.  
- ✅ Configurable paths.  
- ✅ Detailed **logging system** (saves to file + console output).  
- ✅ Support for **batch runs** across multiple input cases (e.g., `gp_01.odb` .. `gp_13.odb`).  

---

## Dependencies
- [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) with Python API (must provide `openroad`, `odb`, `pdn`, `utl` modules).  
- Python **3.8+**.  
- Required Python module in repo:  
  - `mlbuf_buffer_insertion.py` — implements MLBuf buffer insertion logic. Must provide:
    ```python
    def mlbuf_buffer_insertion(design, sta, mlbuf_csv_path, post_tree_csv_path, use_mlbuf: bool, start_cnt: int = 0):
        """
        Should mutate the design by inserting buffers and return:
        (inserted_buffer_count: int, buffer_area: float, buffer_type_distribution: dict)
        """
    ```

---

## Directory Layout
```bash
├── mlbuf_erc_check.py       # Main script (OpenROAD Python driver)
├── python_mlbuf_insertion.py  # MLBuf insertion helper (to be implemented)
├── platforms/                   # Platform libraries
│   └── /                  # Technology name (e.g., nangate45)
│       ├── lib/.lib
│       ├── lef/.lef
│       └── setRC.tcl
├── MLBuf_eval/OR_integration_for_erc/scripts/
│   └── /erc_check_inputs/
│       ├── odb/gp_01.odb
│       ├── mlbuf_results/…
│       ├── vrsz_results/…
│       └── noBuf/…
└── README.md
```
## Usage
### 1. Run with MLBuf
```bash
openroad -python mlbuf_checkERC_main.py \
  --design ibex \
  --tech nangate45
```
### 2. Run with RSZ instead of MLBuf
``` bash
openroad -python mlbuf_checkERC_main.py \
  -d ibex -t nangate45 --no-mlbuf
```

### 3. Skip buffer insertion (only check ERC)
``` bash
openroad -python mlbuf_checkERC_main.py \
  -d ibex -t nangate45 --no-insert
```

### 4. Custom data/platform roots
``` bash
OPENROAD_PLATFORMS=/path/to/platforms \
MLBUF_INPUT_ROOT=/path/to/data \
openroad -python mlbuf_erc_check.py \
  -d ibex -t nangate45 \
  --out-dir results/erc \
  --log results/erc/run.log
```