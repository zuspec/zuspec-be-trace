"""Zuspec Trace Back-end.

Provides components for reading and replaying VCD trace files.
"""
from .vcd_reader import VCDReader, VCDData, VCDSignal, VCDValueChange
from .trace_component import TraceComponentImpl, create_trace_component_class
from .trace_obj_factory import TraceObjFactory, with_trace_replay, VCDDrivenComponent


class TraceComponent:
    """Marker type for trace-backed components.
    
    When a TraceComponent field is declared in an analysis component,
    the TraceObjFactory will replace it with a VCD-driven component
    whose signals come from the trace file.
    
    Usage:
        @zdc.dataclass
        class MyTB(zdc.Component):
            dut : TraceComponent = zdc.inst()
            
            @zdc.sync(clock=lambda s: s.dut.clock)
            def _monitor(self):
                print(f"Value: {self.dut.data}")
    """
    pass


__all__ = [
    'TraceComponent',
    'TraceObjFactory',
    'with_trace_replay',
    'VCDDrivenComponent',
    'VCDReader',
    'VCDData',
    'VCDSignal',
    'VCDValueChange',
    'TraceComponentImpl',
    'create_trace_component_class',
]