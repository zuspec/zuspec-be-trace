
# Zuspec Trace Back-end

The trace back-end dynamically constructs a Zuspec model that mirrors 
the structure (component, input/output, signals) of data within a
trace file (VCD, FST, FSDB). The model uses the specified timebase 
to replay signal values and transitions at the proper points in 
simulated time based on the timestamps in the trace file. This model
is used to perform post-simulation analysis -- for example, creating
transactions from signal-level data.

A special object factory must be provided that is, effectively, a
super-set of the object factory in zuspec-dataclasses/.../rt.

zuspec-dataclasses now contains support for producing VCD files from
the execution of a signal-level Zuspec model. This can be used for
testing.

The trace ObjFactory must be able to identify the target component
via its 'Extern' declaration. The ObjFactory must accept the trace
file. 

During construction, the ObjFactory must identify the component that
represents the trace data.

It must be possible in the future to create more-efficient 'C' 
implementations of the replay logic.
