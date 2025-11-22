#!/usr/bin/env python3

#
# This file is part of LiteX.
#
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 Antmicro <www.antmicro.com>
# SPDX-License-Identifier: BSD-2-Clause

import os
import sys
import json
import argparse
import textwrap

from litex.gen.common import KILOBYTE, MEGABYTE

from litex.soc.cores.dts import DTSNode, DTSRegMode, HexInt, load_csr, get_aliases

# Rename mem according to prefix if provided and add 0 suffix if not provided
def dev_name(dev:str, prefix=None):
    import re
    match = re.search(r'^(.*?)([^a-zA-Z]+)$', dev)
    match_prefix, match_suffix = (match.group(1), match.group(2)) if match else (dev, '0')
    if prefix is None:
        prefix = match_prefix
    return prefix + match_suffix

def generate_dts(d, initrd_start=None, initrd_size=None, initrd=None, root_device=None, polling=False):
    aliases = {}

    # CPU Parameters -------------------------------------------------------------------------------
    cpu_count      = int(d["constants"].get("config_cpu_count", 1))
    cpu_name       = d["constants"].get("config_cpu_name")
    cpu_family     = d["constants"].get("config_cpu_family")
    cpu_isa        = d["constants"].get("config_cpu_isa", None)
    cpu_mmu        = d["constants"].get("config_cpu_mmu", None)
    cpu_interrupts = d["constants"].get("config_cpu_interrupts", 32)

    # DTS nodes -----------------------------------------------------------------------------------
    root_node = DTSNode(None)

    # Header ---------------------------------------------------------------------------------------
    platform = d["constants"]["config_platform_name"]
    root_dts_header = """
compatible = "litex,{platform}", "litex,soc";
model = "{identifier}";
#address-cells = <1>;
#size-cells    = <1>;
""".format(
        platform=platform,
        identifier=d["constants"].get("identifier", platform),
    )

    # Boot Arguments -------------------------------------------------------------------------------

    # Init Ram Disk.
    default_initrd_start = {
        "or1k":   8 * MEGABYTE,
        "riscv": 16 * MEGABYTE,
    }
    default_initrd_size = 8 * MEGABYTE

    if initrd_start is None:
        initrd_start = default_initrd_start[cpu_family]

    if initrd_size is None:
        initrd_size = default_initrd_size

    if initrd == "enabled" or initrd is None:
        initrd_enabled = True
    elif initrd == "disabled":
        initrd_enabled = False
    else:
        initrd_enabled = True
        initrd_size = os.path.getsize(initrd)

    # Root Filesystem.
    if root_device is None:
        root_device = "ram0"

    # Ethernet IP Address.
    def get_eth_ip_config():
        def get_ip_address(prefix):
            return '.'.join(str(d["constants"][f"{prefix}{i+1}"]) for i in range(4))
        ip_config = ""
        if all(f"localip{i + 1}" in d["constants"] for i in range(4)):
            local_ip  = get_ip_address("localip")
            remote_ip = get_ip_address("remoteip")
            ip_config = f" ip={local_ip}:{remote_ip}:{remote_ip}:255.255.255.0::eth0:off:::"
        return ip_config

    # Bootargs Generation.
    root_dts_header += """
chosen {{
    bootargs = "{console} {rootfs}{ip}";""".format(
    console = "console=liteuart earlycon=liteuart,0x{:x}".format(d["csr_bases"]["uart"]),
    rootfs  = "rootwait root=/dev/{}".format(root_device),
    ip      = get_eth_ip_config())

    if initrd_enabled is True:
        root_dts_header += """
    linux,initrd-start = <0x{linux_initrd_start:x}>;
    linux,initrd-end   = <0x{linux_initrd_end:x}>;""".format(
        linux_initrd_start = d["memories"]["main_ram"]["base"] + initrd_start,
        linux_initrd_end   = d["memories"]["main_ram"]["base"] + initrd_start + initrd_size)

    root_dts_header += """
};
"""

    # Clocks ---------------------------------------------------------------------------------------

    for c in [c for c in d["constants"].keys() if c.endswith("config_clock_frequency")]:
        name = c[:len(c) - len("config_clock_frequency")] + "sys_clk"
        freq = d["constants"][c]
        node = root_node + (f"clock-{freq}", name, "fixed-clock", None, DTSRegMode.CUSTOM)
        node.entries["#clock-cells"] = 0
        node.entries["clock-frequency"] = freq

    # CPU ------------------------------------------------------------------------------------------

    # RISC-V
    # ------
    if cpu_family == "riscv":

        def get_riscv_cpu_isa_base(cpu_isa):
            return cpu_isa[:5]

        def get_riscv_cpu_isa_extensions(cpu_isa, cpu_name):
            isa_extensions = set(["i"])

            # Collect common extensions.
            common_extensions = {'i', 'm', 'a', 'f', 'd', 'c'}
            for extension in cpu_isa[5:]:
                if extension in common_extensions:
                    isa_extensions.update({extension})

            # Add rocket-specific extensions.
            if cpu_name == "rocket":
                isa_extensions.update({"zicsr", "zifencei", "zihpm"})

            # Format extensions.
            return ", ".join(f"\"{extension}\"" for extension in sorted(isa_extensions))

        # Cache description.
        cache_desc = ""
        if "config_cpu_dcache_size" in d["constants"]:
            dcache_sets = int(d["constants"]["config_cpu_dcache_size"] /
                              d["constants"]["config_cpu_dcache_block_size"] /
                              d["constants"]["config_cpu_dcache_ways"])
            cache_desc += """
                d-cache-size = <{d_cache_size}>;
                d-cache-sets = <{d_cache_sets}>;
                d-cache-block-size = <{d_cache_block_size}>;
""".format(
    d_cache_size       = d["constants"]["config_cpu_dcache_size"],
    d_cache_sets       = dcache_sets,
    d_cache_block_size = d["constants"]["config_cpu_dcache_block_size"])
        if "config_cpu_icache_size" in d["constants"]:
            icache_sets = int(d["constants"]["config_cpu_icache_size"] /
                              d["constants"]["config_cpu_icache_block_size"] /
                              d["constants"]["config_cpu_icache_ways"])
            cache_desc += """
                i-cache-size = <{i_cache_size}>;
                i-cache-sets = <{i_cache_sets}>;
                i-cache-block-size = <{i_cache_block_size}>;
""".format(
    i_cache_size       = d["constants"]["config_cpu_icache_size"],
    i_cache_sets       = icache_sets,
    i_cache_block_size = d["constants"]["config_cpu_icache_block_size"])
        if "config_cpu_l2cache_size" in d["constants"]:
            cache_desc += """
                next-level-cache = <&cluster0_l2_cache>;
"""

        # TLB description.
        tlb_desc = ""
        if "config_cpu_dtlb_size" in d["constants"]:
            tlb_desc += """
                tlb-split;
                d-tlb-size = <{d_tlb_size}>;
                d-tlb-sets = <{d_tlb_ways}>;
""".format(
    d_tlb_size = d["constants"]["config_cpu_dtlb_size"],
    d_tlb_ways = d["constants"]["config_cpu_dtlb_ways"])
        if "config_cpu_itlb_size" in d["constants"]:
            tlb_desc += """
                i-tlb-size = <{i_tlb_size}>;
                i-tlb-sets = <{i_tlb_ways}>;
""".format(
    i_tlb_size = d["constants"]["config_cpu_itlb_size"],
    i_tlb_ways = d["constants"]["config_cpu_itlb_ways"])

        # Rocket specific attributes
        if (cpu_name == "rocket"):
            extra_attr = """
                hardware-exec-breakpoint-count = <1>;
                next-level-cache = <&memory>;
                riscv,pmpgranularity = <4>;
                riscv,pmpregions = <8>;
"""
        else:
            extra_attr = ""

        # CPU(s) Topology.
        cpu_map = ""
        if cpu_count > 1:
            cpu_map += """
    cpu-map {
        cluster0 {"""
            for cpu in range(cpu_count):
                cpu_map += """
            core{cpu} {{
                cpu = <&CPU{cpu}>;
            }};""".format(cpu=cpu)
            cpu_map += """
        };
    };"""

        l2cache = ""
        if "config_cpu_l2cache_size" in d["constants"]:
            l2_size=d["constants"]["config_cpu_l2cache_size"]
            l2_ways=d["constants"]["config_cpu_l2cache_ways"]
            l2_block_size = d["constants"]["config_cpu_l2cache_block_size"]
            l2_sets = int(l2_size / l2_block_size / l2_ways)
            l2cache += """
	    cluster0_l2_cache: l2-cache0 {{
		compatible = "cache";
		cache-block-size = <{l2block}>;
		cache-level = <2>;
		cache-size = <{l2size}>;
		cache-sets = <{l2sets}>;
		cache-unified;
	    }};""".format(l2size=l2_size, l2block=l2_block_size, l2sets=l2_sets)

        root_dts_header += """
cpus {{
    #address-cells = <1>;
    #size-cells    = <0>;
    timebase-frequency = <{sys_clk_freq}>;
""".format(sys_clk_freq=d["constants"]["config_clock_frequency"])
        for cpu in range(cpu_count):
            root_dts_header += """
    CPU{cpu}: cpu@{cpu} {{
        device_type = "cpu";
        compatible = "riscv";
        riscv,isa = "{cpu_isa}";
        riscv,isa-base = "{cpu_isa_base}";
        riscv,isa-extensions = {cpu_isa_extensions};
        mmu-type = "riscv,{cpu_mmu}";
        reg = <{cpu}>;
        clock-frequency = <{sys_clk_freq}>;
        status = "okay";
        {cache_desc}
        {tlb_desc}
        {extra_attr}
        L{irq}: interrupt-controller {{
            #address-cells = <0>;
            #interrupt-cells = <0x00000001>;
            interrupt-controller;
            compatible = "riscv,cpu-intc";
        }};
    }};
""".format(cpu=cpu, irq=cpu,
    sys_clk_freq       = d["constants"]["config_clock_frequency"],
    cpu_isa            = cpu_isa,
    cpu_isa_base       = get_riscv_cpu_isa_base(cpu_isa),                 # Required for kernel >= 6.6.0
    cpu_isa_extensions = get_riscv_cpu_isa_extensions(cpu_isa, cpu_name), # Required for kernel >= 6.6.0
    cpu_mmu            = cpu_mmu,
    cache_desc         = cache_desc,
    tlb_desc           = tlb_desc,
    extra_attr         = extra_attr)
        root_dts_header += """
    {cpu_map}
    {l2cache}
}};
""".format(cpu_map=cpu_map, l2cache=l2cache)

    # Or1k
    # ----
    elif cpu_family == "or1k":
        root_dts_header += """
cpus {{
    #address-cells = <1>;
    #size-cells = <0>;
    cpu@0 {{
        compatible = "opencores,or1200-rtlsvn481";
        reg = <0>;
        clock-frequency = <{sys_clk_freq}>;
    }};
}};
""".format(sys_clk_freq=d["constants"]["config_clock_frequency"])

    # Memory ---------------------------------------------------------------------------------------

    reserved_memory_node = root_node + "reserved-memory"
    reserved_memory_node.reg_mode = DTSRegMode.CUSTOM
    reserved_memory_node.entries["#address-cells"] = 1
    reserved_memory_node.entries["#size-cells"] = 1
    reserved_memory_node.entries["ranges"] = None

    memory_node = root_node + ("memory", "memory")
    memory_node.reg_mode = DTSRegMode.MEMORY
    memory_node.csr_name = "main_ram"

    if (("opensbi" in d["memories"]) or ("video_framebuffer" in d["csr_bases"])):
        opensbi_node = reserved_memory_node + "opensbi"
        opensbi_node.reg_mode = DTSRegMode.MEMORY

    # SoC ------------------------------------------------------------------------------------------

    soc_node = root_node + ("soc", "soc")
    soc_node.reg_mode = DTSRegMode.CUSTOM

    soc_dts_header = ""
    soc_dts_header += """
#address-cells = <1>;
#size-cells    = <1>;
compatible = "simple-bus";
interrupt-parent = <&intc0>;
ranges;
""".format()

    # Interrupt Controller -------------------------------------------------------------------------

    if (cpu_family == "riscv") and "clint" in d["memories"]:
        # FIXME  : L4 definitiion?
        # CHECKME: interrupts-extended.
        soc_dts_header += """
lintc0: clint@{clint_base:x} {{
    compatible = "riscv,clint0";
    interrupts-extended = <
        {cpu_mapping}>;
    reg = <0x{clint_base:x} 0x10000>;
    reg-names = "control";
}};
""".format(
        clint_base  = d["memories"]["clint"]["base"],
        cpu_mapping = ("\n" + " "*8).join(["&L{} 3 &L{} 7".format(cpu, cpu) for cpu in range(cpu_count)]))
    if cpu_family == "riscv":
        if cpu_name == "rocket":
            extra_attr = """
    reg-names = "control";
    riscv,max-priority = <7>;
"""
        else:
            extra_attr = ""

        soc_dts_header += """
intc0: interrupt-controller@{plic_base:x} {{
    compatible = "sifive,fu540-c000-plic", "sifive,plic-1.0.0";
    reg = <0x{plic_base:x} 0x400000>;
    #address-cells = <0>;
    #interrupt-cells = <1>;
    interrupt-controller;
    interrupts-extended = <
        {cpu_mapping}
    >;
    riscv,ndev = <{interrupt_count}>;
    {extra_attr}
}};
""".format(
        plic_base       = d["memories"]["plic"]["base"],
        cpu_mapping     = ("\n" + " "*8).join(["&L{} 11 &L{} 9".format(cpu, cpu) for cpu in range(cpu_count)]),
        interrupt_count = cpu_interrupts,
        extra_attr      = extra_attr)

    elif cpu_family == "or1k":
        soc_dts_header += """
intc0: interrupt-controller {
    interrupt-controller;
    #interrupt-cells = <1>;
    compatible = "opencores,or1k-pic";
    status = "okay";
};
"""
    if (cpu_family == "riscv") and (cpu_name == "rocket"):
        soc_dts_header += """
dbg_ctl: debug-controller@0 {{
    compatible = "sifive,debug-013", "riscv,debug-013";
    interrupts-extended = <
        {cpu_mapping}>;
    reg = <0x0 0x1000>;
    reg-names = "control";
}};
err_dev: error-device@3000 {{
    compatible = "sifive,error0";
    reg = <0x3000 0x1000>;
}};
ext_it: external-interrupts {{
    interrupts = <1 2 3 4 5 6 7 8>;
}};
rom: rom@10000 {{
    compatible = "sifive,rom0";
    reg = <0x10000 0x10000>;
    reg-names = "mem";
}};
""".format(
        cpu_mapping =("\n" + " "*20).join(["&L{} 0x3F".format(cpu) for cpu in range(cpu_count)]))

    # Clocking  ------------------------------------------------------------------------------------

    def add_clkout(clkout_nr, clk_f, clk_p, clk_dn, clk_dd, clk_margin, clk_margin_exp):
        return """
                CLKOUT{clkout_nr}: CLKOUT{clkout_nr} {{
                    compatible = "litex,clk";
                    #clock-cells = <0>;
                    clock-output-names = "CLKOUT{clkout_nr}";
                    reg = <{clkout_nr}>;
                    litex,clock-frequency = <{clk_f}>;
                    litex,clock-phase = <{clk_p}>;
                    litex,clock-duty-num = <{clk_dn}>;
                    litex,clock-duty-den = <{clk_dd}>;
                    litex,clock-margin = <{clk_margin}>;
                    litex,clock-margin-exp = <{clk_margin_exp}>;
                }};
""".format(
    clkout_nr      = clkout_nr,
    clk_f          = clk_f,
    clk_p          = clk_p,
    clk_dn         = clk_dn,
    clk_dd         = clk_dd,
    clk_margin     = clk_margin,
    clk_margin_exp = clk_margin_exp)

    if "mmcm" in d["csr_bases"]:
        nclkout = d["constants"]["nclkout"]
        soc_dts_header += """
clk0: clk@{mmcm_csr_base:x} {{
    compatible = "litex,clk";
    reg = <0x{mmcm_csr_base:x} 0x100>;
    #clock-cells = <1>;
    #address-cells = <1>;
    #size-cells = <0>;
    clock-output-names =
""".format(mmcm_csr_base=d["csr_bases"]["mmcm"])
        for clkout_nr in range(nclkout - 1):
            soc_dts_header += """
        "CLKOUT{clkout_nr}",
""".format(clkout_nr=clkout_nr)
        soc_dts_header += """
        "CLKOUT{nclkout}";
""".format(nclkout=(nclkout - 1))
        soc_dts_header += """
    litex,lock-timeout = <{mmcm_lock_timeout}>;
    litex,drdy-timeout = <{mmcm_drdy_timeout}>;
    litex,sys-clock-frequency = <{sys_clk}>;
    litex,divclk-divide-min = <{divclk_divide_range[0]}>;
    litex,divclk-divide-max = <{divclk_divide_range[1]}>;
    litex,clkfbout-mult-min = <{clkfbout_mult_frange[0]}>;
    litex,clkfbout-mult-max = <{clkfbout_mult_frange[1]}>;
    litex,vco-freq-min = <{vco_freq_range[0]}>;
    litex,vco-freq-max = <{vco_freq_range[1]}>;
    litex,clkout-divide-min = <{clkout_divide_range[0]}>;
    litex,clkout-divide-max = <{clkout_divide_range[1]}>;
    litex,vco-margin = <{vco_margin}>;
""".format(
    mmcm_lock_timeout    =  d["constants"]["mmcm_lock_timeout"],
    mmcm_drdy_timeout    =  d["constants"]["mmcm_drdy_timeout"],
    sys_clk              =  d["constants"]["config_clock_frequency"],
    divclk_divide_range  = (d["constants"]["divclk_divide_range_min"], d["constants"]["divclk_divide_range_max"]),
    clkfbout_mult_frange = (d["constants"]["clkfbout_mult_frange_min"], d["constants"]["clkfbout_mult_frange_max"]),
    vco_freq_range       = (d["constants"]["vco_freq_range_min"], d["constants"]["vco_freq_range_max"]),
    clkout_divide_range  = (d["constants"]["clkout_divide_range_min"], d["constants"]["clkout_divide_range_max"]),
    vco_margin           =  d["constants"]["vco_margin"])
        for clkout_nr in range(nclkout):
            soc_dts_header += add_clkout(clkout_nr,
                d["constants"]["clkout_def_freq"],
                d["constants"]["clkout_def_phase"],
                d["constants"]["clkout_def_duty_num"],
                d["constants"]["clkout_def_duty_den"],
                d["constants"]["clkout_margin"],
                d["constants"]["clkout_margin_exp"])
        soc_dts_header += """
            };"""

    soc_dts_header += f"\n/* Generated from DTSBase */\n"

    load_csr(d, root_node, polling)

    # Aliases --------------------------------------------------------------------------------------
    aliases.update(get_aliases(root_node))

    root_dts_footer = ""
    if aliases:
        root_dts_footer += "\naliases {\n"
        for alias in aliases:
            root_dts_footer += "    {} = &{};\n".format(alias, aliases[alias])
        root_dts_footer += "};\n"

    root_node.header = root_dts_header
    root_node.footer = root_dts_footer
    soc_node.header = soc_dts_header

    return "/dts-v1/;\n/" + root_node.generate()

def main():

    parser = argparse.ArgumentParser(description="LiteX's CSR JSON to Linux DTS generator")
    parser.add_argument("csr_json", help="CSR JSON file")
    parser.add_argument("--initrd-start", type=int,            help="Location of initrd in RAM (relative, default depends on CPU).")
    parser.add_argument("--initrd-size",  type=int,            help="Size of initrd (default=8MB).")
    parser.add_argument("--initrd",       type=str,            help="Supports arguments 'enabled', 'disabled' or a file name. Set to 'disabled' if you use a kernel built in rootfs or have your rootfs on an SD card partition. If a file name is provied the size of the file will be used instead of --initrd-size. (default=enabled).")
    parser.add_argument("--root-device",  type=str,            help="Device that has our rootfs, if using initrd use the default. For SD card's use something like mmcblk0p3. (default=ram0).")
    parser.add_argument("--polling",      action="store_true", help="Force polling mode on peripherals.")
    args = parser.parse_args()

    d = json.load(open(args.csr_json))
    r = generate_dts(d,
        initrd_start = args.initrd_start,
        initrd_size  = args.initrd_size,
        initrd       = args.initrd,
        root_device  = args.root_device,
        polling      = args.polling,
    )
    print(r)

if __name__ == "__main__":
    main()
