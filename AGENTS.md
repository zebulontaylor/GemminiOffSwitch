# Hardware build resources

Use the guarded project entry points for synthesis, netlist extraction, and RTL elaboration. For an ad hoc Yosys, ABC, firtool, or Scala hardware build, invoke `scripts/hardware_guard.py COMMAND ...`. Run one build at a time. If the kernel limit stops a job, reduce the synthesis partition or move it to a memory-limited compute job. Keep the workstation available for interactive use.
