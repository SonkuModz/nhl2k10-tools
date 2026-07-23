@echo off
REM Headless-decompile the VC decompressor functions from the XEX basefile.
REM Imports extracted\default_base.bin as raw PowerPC BE:64 @ 0x82000000, runs the
REM decompile post-script. MUST be BE:64 -- as BE:32 the 64-bit ldx/std decode as
REM bad data and the decompiler output is silently truncated.
REM decompile post-script, and writes docs\ghidra_decompiled.c.
setlocal
set ROOT=%~dp0
set GHIDRA=%ROOT%ghidra_12.1.2_PUBLIC
set PROJDIR=%ROOT%ghidra_proj
if not exist "%PROJDIR%" mkdir "%PROJDIR%"

"%GHIDRA%\support\analyzeHeadless.bat" "%PROJDIR%" nhl2k10 ^
  -import "%ROOT%extracted\default_base.bin" ^
  -loader BinaryLoader -loader-baseAddr 0x82000000 ^
  -processor "PowerPC:BE:64:default" ^
  -noanalysis ^
  -scriptPath "%ROOT%tools" ^
  -postScript ghidra_decompile_targets.py ^
  -deleteProject
endlocal
