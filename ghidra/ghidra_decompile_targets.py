# Ghidra headless post-script: decompile the VC decompressor functions to C.
# @category NHL2K10
#
# Run via analyzeHeadless (see run_ghidra_headless.bat). Assumes the flat XEX
# basefile was imported as PowerPC:BE:64 at image base 0x82000000 (BE:32 makes the
# 64-bit ldx/std decode as bad data and truncates the output). Disassembles
# and creates a function at each target address, decompiles it, and writes the C
# to docs/ghidra_decompiled.c.
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

TARGETS = [
    ("variant6_B800",  0x8414B800),
    ("variant5_B380",  0x8414B380),
    ("variant4_AF00",  0x8414AF00),
    ("variant3_AA80",  0x8414AA80),
    ("variant2_A600",  0x8414A600),
    ("variant1_A180",  0x8414A180),
    ("variant0_9D00",  0x84149D00),
    ("dispatch_setup",  0x8414C000),
    ("isCompressed_BC80", 0x8414BC80),
]

af = currentProgram.getAddressFactory().getDefaultAddressSpace()
fm = currentProgram.getFunctionManager()
monitor = ConsoleTaskMonitor()
decomp = DecompInterface()
decomp.openProgram(currentProgram)

lines = []
for name, va in TARGETS:
    addr = af.getAddress(va)
    fn = fm.getFunctionAt(addr)
    if fn is None:
        try:
            disassemble(addr)
        except Exception as e:
            pass
        try:
            fn = createFunction(addr, name)
        except Exception as e:
            fn = fm.getFunctionContaining(addr)
    if fn is None:
        lines.append("// %s @ 0x%08X : could not create function\n" % (name, va))
        continue
    try:
        fn.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    res = decomp.decompileFunction(fn, 120, monitor)
    if res is not None and res.decompileCompleted():
        c = res.getDecompiledFunction().getC()
    else:
        c = "// decompile failed: %s" % (res.getErrorMessage() if res else "no result")
    lines.append("// ===== %s @ 0x%08X =====\n%s\n" % (name, va, c))

import os
# repo-relative, so this works from any clone
_here = os.path.dirname(os.path.abspath(__file__))
out_path = os.environ.get(
    "NHL2K10_DECOMP_OUT",
    os.path.join(os.path.dirname(_here), "docs", "ghidra_decompiled.c"))
f = open(out_path, "w")
f.write("\n".join(lines))
f.close()
print("WROTE " + out_path)
