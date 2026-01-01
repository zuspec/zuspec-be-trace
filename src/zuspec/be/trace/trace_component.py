"""Trace component implementation for VCD playback.

TraceComponent dynamically creates signals from a VCD file and replays
their values during simulation.
"""
import dataclasses as dc
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from .vcd_reader import VCDReader, VCDData, VCDSignal, VCDValueChange

if TYPE_CHECKING:
    from zuspec.dataclasses.rt.timebase import Timebase


@dc.dataclass
class TraceComponentImpl:
    """Runtime implementation for TraceComponent.
    
    Manages signal values and schedules value changes during simulation.
    """
    _vcd_data: VCDData = dc.field()
    _signal_values: Dict[str, int] = dc.field(default_factory=dict)
    _signal_widths: Dict[str, int] = dc.field(default_factory=dict)
    _change_idx: int = dc.field(default=0)
    _timebase: Optional['Timebase'] = dc.field(default=None)
    _parent_comp: Any = dc.field(default=None)  # Reference to the TraceComponent
    
    def __post_init__(self):
        # Initialize signal values from VCD initial values
        for identifier, signal in self._vcd_data.signals.items():
            full_path = f"{signal.scope_path}.{signal.name}" if signal.scope_path else signal.name
            # Use short name for signal access
            self._signal_widths[signal.name] = signal.width
            
            # Get initial value if available
            if identifier in self._vcd_data.initial_values:
                self._signal_values[signal.name] = self._vcd_data.initial_values[identifier]
            else:
                self._signal_values[signal.name] = 0
    
    def get_signal(self, name: str) -> int:
        """Get current value of a signal."""
        return self._signal_values.get(name, 0)
    
    def get_signal_names(self) -> List[str]:
        """Get list of all signal names."""
        return list(self._signal_values.keys())
    
    def get_signal_width(self, name: str) -> int:
        """Get width of a signal."""
        return self._signal_widths.get(name, 1)
    
    def schedule_changes(self, timebase: 'Timebase'):
        """Schedule all value changes on the timebase."""
        from zuspec.dataclasses import Time
        
        self._timebase = timebase
        
        # Group changes by time
        changes_by_time: Dict[float, List[VCDValueChange]] = {}
        for change in self._vcd_data.value_changes:
            if change.time_ns not in changes_by_time:
                changes_by_time[change.time_ns] = []
            changes_by_time[change.time_ns].append(change)
        
        # Schedule each time group
        for time_ns, changes in sorted(changes_by_time.items()):
            # Create callback for this time
            def make_callback(changes_at_time):
                def apply_changes():
                    for change in changes_at_time:
                        signal = self._vcd_data.signals.get(change.identifier)
                        if signal:
                            self._signal_values[signal.name] = change.value
                return apply_changes
            
            timebase.after(Time.ns(time_ns), make_callback(changes))
    
    def advance_to(self, time_ns: float):
        """Advance simulation and apply all changes up to the given time."""
        while self._change_idx < len(self._vcd_data.value_changes):
            change = self._vcd_data.value_changes[self._change_idx]
            if change.time_ns > time_ns:
                break
            
            signal = self._vcd_data.signals.get(change.identifier)
            if signal:
                self._signal_values[signal.name] = change.value
            
            self._change_idx += 1


class DynamicSignalDescriptor:
    """Property descriptor for dynamic signal access on TraceComponent."""
    
    def __init__(self, name: str):
        self.name = name
    
    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        
        if hasattr(obj, '_trace_impl') and obj._trace_impl is not None:
            return obj._trace_impl.get_signal(self.name)
        return 0
    
    def __set__(self, obj, value):
        # TraceComponent signals are read-only (driven from VCD)
        raise AttributeError(f"Cannot write to trace signal '{self.name}'")


def create_trace_component_class(vcd_data: VCDData, base_class=None):
    """Dynamically create a TraceComponent class with signals from VCD.
    
    Creates a new class with property descriptors for each signal in the VCD.
    """
    import zuspec.dataclasses as zdc
    
    # Build class namespace with signal descriptors
    namespace = {}
    annotations = {}
    
    for identifier, signal in vcd_data.signals.items():
        # Create a descriptor for this signal
        namespace[signal.name] = DynamicSignalDescriptor(signal.name)
        # Annotate with appropriate type based on width
        if signal.width == 1:
            annotations[signal.name] = zdc.bit
        elif signal.width <= 8:
            annotations[signal.name] = zdc.u8
        elif signal.width <= 16:
            annotations[signal.name] = zdc.u16
        elif signal.width <= 32:
            annotations[signal.name] = zdc.u32
        else:
            annotations[signal.name] = zdc.u64
    
    namespace['__annotations__'] = annotations
    
    # Create the class
    if base_class is None:
        from zuspec.dataclasses import Component
        base_class = Component
    
    # Create a simple class that holds the trace impl
    class TraceComponentRuntime:
        """Runtime trace component with signals from VCD."""
        
        def __init__(self, vcd_data: VCDData):
            object.__setattr__(self, '_trace_impl', TraceComponentImpl(_vcd_data=vcd_data))
            object.__setattr__(self, '_impl', None)
        
        def __getattr__(self, name):
            trace_impl = object.__getattribute__(self, '_trace_impl')
            if trace_impl and name in trace_impl._signal_values:
                return trace_impl.get_signal(name)
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        
        def schedule_changes(self, timebase):
            """Schedule VCD value changes on the timebase."""
            self._trace_impl.schedule_changes(timebase)
    
    return TraceComponentRuntime
