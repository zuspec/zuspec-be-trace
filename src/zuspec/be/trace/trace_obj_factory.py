"""Object factory for trace-based components.

TraceObjFactory extends the standard ObjFactory to handle TraceComponent
fields by creating dynamic components from VCD trace files.
"""
import dataclasses as dc
from contextlib import contextmanager
from typing import Dict, Type, Optional, Any, List, Tuple, Set
from .vcd_reader import VCDReader, VCDData


class VCDDrivenComponent:
    """A component whose signals are driven from VCD data.
    
    This acts as a proxy that provides signal values from VCD replay.
    Signal values are accessed via attribute access (e.g., comp.clock, comp.count).
    """
    
    def __init__(self, vcd_data: VCDData):
        object.__setattr__(self, '_vcd_data', vcd_data)
        object.__setattr__(self, '_signal_values', {})
        object.__setattr__(self, '_change_idx', 0)
        
        # Initialize from VCD initial values
        for identifier, value in vcd_data.initial_values.items():
            signal = vcd_data.signals.get(identifier)
            if signal:
                self._signal_values[signal.name] = value
        
        # Initialize any signals not in initial values to 0
        for identifier, signal in vcd_data.signals.items():
            if signal.name not in self._signal_values:
                self._signal_values[signal.name] = 0
    
    def __getattr__(self, name):
        signal_values = object.__getattribute__(self, '_signal_values')
        if name in signal_values:
            return signal_values[name]
        raise AttributeError(f"'{type(self).__name__}' has no signal '{name}'")
    
    def __setattr__(self, name, value):
        # Signals are read-only - driven from VCD
        if not name.startswith('_'):
            raise AttributeError(f"Cannot write to VCD-driven signal '{name}'")
        object.__setattr__(self, name, value)
    
    def _advance_to(self, time_ns: float):
        """Advance and apply all VCD changes up to the given time."""
        vcd_data = object.__getattribute__(self, '_vcd_data')
        signal_values = object.__getattribute__(self, '_signal_values')
        change_idx = object.__getattribute__(self, '_change_idx')
        
        while change_idx < len(vcd_data.value_changes):
            change = vcd_data.value_changes[change_idx]
            if change.time_ns > time_ns:
                break
            
            signal = vcd_data.signals.get(change.identifier)
            if signal:
                signal_values[signal.name] = change.value
            
            change_idx += 1
        
        object.__setattr__(self, '_change_idx', change_idx)
    
    def _get_signal_names(self) -> List[str]:
        """Get list of all signal names."""
        return list(object.__getattribute__(self, '_signal_values').keys())


class TraceObjFactory:
    """Object factory that creates components with TraceComponent fields driven from VCD.
    
    When a component has a field of type TraceComponent, this factory creates
    a VCD-driven component for that field. The parent component's @sync/@comb
    processes can then observe the VCD-driven signals.
    
    Usage:
        with with_trace_replay(vcd_file="trace.vcd") as factory:
            tb = MyTestbench()  # tb.counter signals come from VCD
        
        # Run replay - tb's @sync processes observe VCD values
        factory.run_replay(tb)
    """
    
    def __init__(self, vcd_file: str):
        """Initialize factory with VCD file.
        
        Args:
            vcd_file: Path to VCD file to read
        """
        self._vcd_file = vcd_file
        self._vcd_data = None
        self._vcd_driven_components: List[VCDDrivenComponent] = []
        self._testbench_components: List[Any] = []
        
        # Parse VCD file
        reader = VCDReader(vcd_file)
        self._vcd_data = reader.parse()
    
    def mkComponent(self, cls: Type, **kwargs):
        """Create a component, replacing TraceComponent fields with VCD-driven components."""
        from zuspec.dataclasses.rt import ObjFactory
        from zuspec.be.trace import TraceComponent
        
        # Check if this class has TraceComponent fields
        trace_field_names = []
        
        for f in dc.fields(cls):
            field_type = f.type
            if field_type is TraceComponent:
                trace_field_names.append(f.name)
        
        # If there are TraceComponent fields, we need to hook into post-initialization
        if trace_field_names:
            # Store what needs to be set
            vcd_components = {}
            for field_name in trace_field_names:
                vcd_comp = VCDDrivenComponent(self._vcd_data)
                vcd_components[field_name] = vcd_comp
                self._vcd_driven_components.append(vcd_comp)
            
            # Store for later application (will be set after init completes)
            self._pending_vcd_assignments = vcd_components
        
        # Use standard factory to create the component
        standard_factory = ObjFactory.inst()
        comp = standard_factory.mkComponent(cls, **kwargs)
        
        # Note: At this point, comp.__init__ hasn't run yet!
        # We'll set the fields in the context manager's finally block
        
        if trace_field_names:
            self._testbench_components.append(comp)
        
        return comp
    
    def _finalize_components(self):
        """Apply VCD components to testbench fields after initialization is complete."""
        if hasattr(self, '_pending_vcd_assignments') and self._pending_vcd_assignments:
            for comp in self._testbench_components:
                for field_name, vcd_comp in self._pending_vcd_assignments.items():
                    if hasattr(comp, field_name):
                        setattr(comp, field_name, vcd_comp)
                        # Also update _impl's signal_values if tracked
                        if hasattr(comp, '_impl') and comp._impl and hasattr(comp._impl, '_signal_values'):
                            if field_name in comp._impl._signal_values:
                                comp._impl._signal_values[field_name] = vcd_comp
    
    def mkEvent(self, cls: Type, **kwargs):
        """Delegate event creation to standard factory."""
        from zuspec.dataclasses.rt import ObjFactory
        return ObjFactory.inst().mkEvent(cls, **kwargs)
    
    def run_replay(self, comp: Any = None):
        """Run the VCD replay, driving signals and triggering @sync processes.
        
        Steps through all VCD timestamps, updates signal values, and
        triggers clock edges to run @sync processes on the testbench.
        
        Args:
            comp: The testbench component (uses first created if not specified)
        """
        if comp is None:
            if not self._testbench_components:
                return
            comp = self._testbench_components[0]
        
        # Get unique timestamps from VCD
        timestamps = set()
        for change in self._vcd_data.value_changes:
            timestamps.add(change.time_ns)
        
        # Process each timestamp in order
        for time_ns in sorted(timestamps):
            # Advance all VCD-driven components to this time
            for vcd_comp in self._vcd_driven_components:
                vcd_comp._advance_to(time_ns)
            
            # Trigger @sync processes on the testbench
            self._trigger_sync_processes(comp, time_ns)
    
    def _trigger_sync_processes(self, comp: Any, time_ns: float):
        """Trigger @sync processes if their clock signal has a rising edge."""
        if not hasattr(comp, '_impl') or comp._impl is None:
            return
        
        impl = comp._impl
        
        # Execute sync processes
        from zuspec.dataclasses.rt.comp_impl_rt import EvalMode
        for sync_func in impl._sync_processes:
            impl._eval_mode = EvalMode.SYNC_EVAL
            impl._execute_function(comp, sync_func)
            impl._eval_mode = EvalMode.IDLE
    
    def get_value_at_time(self, signal_name: str, time_ns: float) -> int:
        """Get the value of a signal at a specific time."""
        identifier = None
        for ident, signal in self._vcd_data.signals.items():
            if signal.name == signal_name:
                identifier = ident
                break
        
        if identifier is None:
            raise ValueError(f"Signal '{signal_name}' not found in VCD")
        
        value = self._vcd_data.initial_values.get(identifier, 0)
        
        for change in self._vcd_data.value_changes:
            if change.time_ns > time_ns:
                break
            if change.identifier == identifier:
                value = change.value
        
        return value
    
    def get_all_timestamps(self) -> List[float]:
        """Get list of all unique timestamps in the VCD."""
        timestamps = set()
        for change in self._vcd_data.value_changes:
            timestamps.add(change.time_ns)
        return sorted(timestamps)
    
    @property
    def vcd_data(self):
        """Get the parsed VCD data."""
        return self._vcd_data


@contextmanager
def with_trace_replay(vcd_file: str):
    """Context manager to enable VCD trace replay for component construction.
    
    Components with TraceComponent fields will have those fields replaced
    with VCD-driven components. The parent component's @sync processes can
    observe the VCD-driven signals.
    
    Usage:
        @zdc.dataclass
        class MyTB(zdc.Component):
            counter : TraceComponent = zdc.field(default=None)
            
            @zdc.sync(clock=lambda s: s.counter.clock)
            def _monitor(self):
                print(f"Count: {self.counter.count}")
        
        with with_trace_replay("trace.vcd") as factory:
            tb = MyTB()
        
        factory.run_replay(tb)  # Drives counter signals, triggers _monitor
    
    Args:
        vcd_file: Path to VCD file containing trace data
        
    Yields:
        TraceObjFactory instance for replay control
    """
    from zuspec.dataclasses.config import Config
    
    factory = TraceObjFactory(vcd_file)
    config = Config.inst()
    config.push_factory(factory)
    
    try:
        yield factory
    finally:
        config.pop_factory()
        # Apply VCD components to fields after all initialization is complete
        factory._finalize_components()

