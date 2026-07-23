// Ghidra headless script: decompile the VC decompressor functions to C.
// @category NHL2K10
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.util.task.ConsoleTaskMonitor;
import java.io.PrintWriter;

public class GhidraDecompileTargets extends GhidraScript {
    public void run() throws Exception {
        long[] targets = {
            0x8414B800L, 0x8414B380L, 0x8414AF00L, 0x8414AA80L,
            0x8414A600L, 0x8414A180L, 0x84149D00L, 0x8414C000L, 0x8414BC80L
        };
        String[] names = {
            "variant6_B800", "variant5_B380", "variant4_AF00", "variant3_AA80",
            "variant2_A600", "variant1_A180", "variant0_9D00",
            "dispatch_setup_C000", "isCompressed_BC80"
        };
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        FunctionManager fm = currentProgram.getFunctionManager();
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < targets.length; i++) {
            Address a = sp.getAddress(targets[i]);
            Function fn = fm.getFunctionAt(a);
            if (fn == null) {
                try { disassemble(a); } catch (Exception e) {}
                try { fn = createFunction(a, names[i]); } catch (Exception e) {}
            }
            if (fn == null) fn = fm.getFunctionContaining(a);
            if (fn == null) {
                sb.append("// " + names[i] + " @ 0x" + Long.toHexString(targets[i])
                          + " : could not create function\n\n");
                continue;
            }
            DecompileResults r = d.decompileFunction(fn, 180, new ConsoleTaskMonitor());
            String c;
            if (r != null && r.decompileCompleted())
                c = r.getDecompiledFunction().getC();
            else
                c = "// decompile failed: " + (r != null ? r.getErrorMessage() : "null");
            sb.append("// ===== " + names[i] + " @ 0x" + Long.toHexString(targets[i])
                      + " =====\n" + c + "\n");
        }
        // Output path: NHL2K10_DECOMP_OUT if set, else ./ghidra_decompiled.c in
        // the working directory. Kept repo-relative so this runs from any clone.
        String out = System.getenv("NHL2K10_DECOMP_OUT");
        if (out == null || out.isEmpty())
            out = "ghidra_decompiled.c";
        PrintWriter pw = new PrintWriter(out);
        pw.print(sb.toString());
        pw.close();
        println("WROTE " + out);
    }
}
