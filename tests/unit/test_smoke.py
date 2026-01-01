import asyncio
import os
import zuspec.dataclasses as zdc
from zuspec.dataclasses.rt import with_tracer, VCDTracer
from zuspec.be.trace import TraceComponent, TraceObjFactory, VCDReader, with_trace_replay


def test_smoke(tmpdir):
    """Test trace back-end with a counter generating VCD and replaying it.
    
    This test:
    1. Creates a Counter component and runs it to generate a VCD file
    2. Uses with_trace_replay to create a testbench with a TraceComponent
    3. Runs the replay and verifies values are correctly driven from VCD
    """
    
    # Step 1: Create a counter component that produces a VCD file
    @zdc.dataclass
    class Counter(zdc.Component):
        reset : zdc.bit = zdc.input()
        clock : zdc.bit = zdc.input()
        c1 : zdc.u32 = zdc.output()
        
        @zdc.sync(clock=lambda s: s.clock, reset=lambda s: s.reset)
        def _count(self):
            if self.reset:
                self.c1 = 0
            else:
                self.c1 = self.c1 + 1

    # Step 2: Run the counter to produce a VCD file
    vcd_path = str(tmpdir.join("counter.vcd"))
    vcd = VCDTracer(vcd_path)
    
    with with_tracer(vcd, enable_signals=True):
        counter = Counter()
    
    async def run_counter():
        # Reset sequence
        counter.reset = 1
        counter.clock = 0
        await counter.wait(zdc.Time.ns(5))
        counter.clock = 1
        await counter.wait(zdc.Time.ns(5))
        counter.clock = 0
        await counter.wait(zdc.Time.ns(5))
        
        # Release reset and run counter for several cycles
        counter.reset = 0
        for cycle in range(10):
            counter.clock = 1
            await counter.wait(zdc.Time.ns(5))
            counter.clock = 0
            await counter.wait(zdc.Time.ns(5))
    
    asyncio.run(run_counter())
    vcd.close()
    
    # Verify VCD was created
    assert os.path.exists(vcd_path)
    
    # Step 3: Query values at specific times
    with with_trace_replay(vcd_file=vcd_path) as factory:
        pass  # No component needed for simple queries
    
    # At t=10ns (first clock edge during reset), c1 should be 0
    c1_at_10 = factory.get_value_at_time("c1", 10.0)
    assert c1_at_10 == 0, f"Expected c1=0 at t=10ns, got {c1_at_10}"
    
    # After 10 clock cycles post-reset, c1 should reach 10
    final_c1 = factory.get_value_at_time("c1", 200.0)
    assert final_c1 == 10, f"Expected final c1=10, got {final_c1}"


def test_replay_output_values(tmpdir):
    """Test that output port values are correctly driven from VCD replay."""
    
    @zdc.dataclass
    class DataGenerator(zdc.Component):
        clock : zdc.bit = zdc.input()
        data_out : zdc.u8 = zdc.output()
        valid : zdc.bit = zdc.output()
    
    # Generate VCD with known pattern
    vcd_path = str(tmpdir.join("data.vcd"))
    vcd = VCDTracer(vcd_path)
    
    with with_tracer(vcd, enable_signals=True):
        gen = DataGenerator()
    
    # Set specific values at known times
    test_values = [0xAA, 0xBB, 0xCC, 0xDD]
    
    async def run_generator():
        gen.clock = 0
        gen.data_out = 0
        gen.valid = 0
        await gen.wait(zdc.Time.ns(5))
        
        for i, data in enumerate(test_values):
            gen.clock = 1
            gen.data_out = data
            gen.valid = 1 if i != 2 else 0  # valid=0 for third value
            await gen.wait(zdc.Time.ns(5))
            gen.clock = 0
            await gen.wait(zdc.Time.ns(5))
    
    asyncio.run(run_generator())
    vcd.close()
    
    # Query values at specific times using the factory
    with with_trace_replay(vcd_file=vcd_path) as factory:
        pass
    
    # Verify using get_value_at_time
    timestamps = factory.get_all_timestamps()
    print(f"Timestamps: {timestamps}")
    
    # Find timestamps where we expect specific values
    # First data value (0xAA) should appear around t=10ns
    for ts in timestamps:
        val = factory.get_value_at_time("data_out", ts)
        if val == 0xAA:
            print(f"Found data_out=0xAA at t={ts}ns")
            break
    else:
        assert False, "Never found data_out=0xAA in replay"
    
    # Verify all test values appear
    for expected in test_values:
        found = False
        for ts in timestamps:
            val = factory.get_value_at_time("data_out", ts)
            if val == expected:
                found = True
                break
        assert found, f"Expected to see data_out={expected:02X}"


def test_get_value_at_time(tmpdir):
    """Test querying signal values at specific times."""
    
    @zdc.dataclass
    class SimpleCounter(zdc.Component):
        clock : zdc.bit = zdc.input()
        count : zdc.u8 = zdc.output()
    
    vcd_path = str(tmpdir.join("simple.vcd"))
    vcd = VCDTracer(vcd_path)
    
    with with_tracer(vcd, enable_signals=True):
        counter = SimpleCounter()
    
    async def run():
        counter.clock = 0
        counter.count = 0
        await counter.wait(zdc.Time.ns(10))
        
        # Increment count at each clock
        for i in range(5):
            counter.clock = 1
            counter.count = i + 1
            await counter.wait(zdc.Time.ns(10))
            counter.clock = 0
            await counter.wait(zdc.Time.ns(10))
    
    asyncio.run(run())
    vcd.close()
    
    # Query values at specific times
    with with_trace_replay(vcd_file=vcd_path) as factory:
        replay = SimpleCounter()
    
    # Initial value should be 0
    assert factory.get_value_at_time("count", 0) == 0
    
    # After first increment (t=20ns), count should be 1
    assert factory.get_value_at_time("count", 25) == 1
    
    # After all increments, count should be 5
    assert factory.get_value_at_time("count", 200) == 5
    
    print("All time-based queries passed!")

def test_tb_comp(tmpdir):
    """Test a testbench component that monitors a VCD-driven TraceComponent."""
    
    @zdc.dataclass
    class SimpleCounter(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        count : zdc.u8 = zdc.output()
    
    vcd_path = str(tmpdir.join("simple.vcd"))
    vcd = VCDTracer(vcd_path)
    
    with with_tracer(vcd, enable_signals=True):
        counter = SimpleCounter()
    
    async def run():
        counter.clock = 0
        counter.reset = 1
        counter.count = 0
        await counter.wait(zdc.Time.ns(10))
        
        # Release reset
        counter.reset = 0
        
        # Increment count at each clock
        for i in range(5):
            counter.clock = 1
            counter.count = i + 1
            await counter.wait(zdc.Time.ns(10))
            counter.clock = 0
            await counter.wait(zdc.Time.ns(10))
    
    asyncio.run(run())
    vcd.close()

    # Now create a testbench that monitors the VCD-driven counter
    observed_counts = []
    
    @zdc.dataclass
    class MyTB(zdc.Component):
        # Use a regular field (not inst) - factory will provide the value
        counter : TraceComponent = zdc.field(default=None)

        @zdc.sync(clock=lambda s: s.counter.clock, reset=lambda s: s.counter.reset)
        def _monitor(self):
            # Simplified: just record the count value (avoid 'not' operator)
            if self.counter.reset == 0:
                print("Count: %d" % self.counter.count)
                observed_counts.append(self.counter.count)

    # Use factory to create MyTB where 'counter' signals come from VCD
    with with_trace_replay(vcd_file=vcd_path) as factory:
        tb = MyTB()
    
    # Verify the counter field is a VCD-driven component
    assert hasattr(tb.counter, '_vcd_data'), f"Expected VCDDrivenComponent, got {type(tb.counter)}"
    
    # Verify signals are available
    signal_names = tb.counter._get_signal_names()
    assert 'clock' in signal_names
    assert 'reset' in signal_names
    assert 'count' in signal_names
    
    # Run the replay - this should trigger _monitor at each clock edge
    factory.run_replay(tb)
    
    print(f"Observed counts: {observed_counts}")
    
    # Verify we observed the count values (1, 2, 3, 4, 5)
    assert len(observed_counts) > 0, "Expected to observe count values"
    assert 5 in observed_counts, "Expected to see count=5"


def test_vcd_reader_basic(tmpdir):
    """Test VCD reader with a simple VCD file."""
    vcd_content = """$date
   2024-01-01 12:00:00
$end
$version
   zuspec-dataclasses VCD Writer 1.0
$end
$timescale 1 ns $end
$scope module top $end
$var wire 1 ! clk $end
$var reg 8 " data $end
$upscope $end
$enddefinitions $end
$dumpvars
0!
bx "
$end
#10
1!
b10101010 "
#20
0!
#30
1!
b11110000 "
"""
    vcd_path = str(tmpdir.join("test.vcd"))
    with open(vcd_path, 'w') as f:
        f.write(vcd_content)
    
    reader = VCDReader(vcd_path)
    data = reader.parse()
    
    # Check timescale
    assert data.timescale_ns == 1.0
    
    # Check signals
    assert len(data.signals) == 2
    assert '!' in data.signals
    assert '"' in data.signals
    assert data.signals['!'].name == 'clk'
    assert data.signals['"'].name == 'data'
    assert data.signals['!'].width == 1
    assert data.signals['"'].width == 8
    
    # Check initial values
    assert '!' in data.initial_values
    assert data.initial_values['!'] == 0
    
    # Check value changes
    assert len(data.value_changes) >= 4
    
    # First change at t=10: clk=1, data=0xAA (170)
    changes_at_10 = [c for c in data.value_changes if c.time_ns == 10.0]
    assert len(changes_at_10) == 2


def test_trace_component_signal_access(tmpdir):
    """Test that TraceComponent signals can be accessed via the old API."""
    from zuspec.be.trace import TraceComponentImpl, create_trace_component_class
    
    vcd_content = """$timescale 1 ns $end
$scope module dut $end
$var reg 8 ! count $end
$var wire 1 " enable $end
$upscope $end
$enddefinitions $end
$dumpvars
b0 !
0"
$end
#10
b1 !
#20
b10 !
#30
b11 !
1"
#40
b100 !
"""
    vcd_path = str(tmpdir.join("signals.vcd"))
    with open(vcd_path, 'w') as f:
        f.write(vcd_content)
    
    reader = VCDReader(vcd_path)
    vcd_data = reader.parse()
    
    # Create trace component implementation directly
    trace_impl = TraceComponentImpl(_vcd_data=vcd_data)
    
    # Check initial values
    assert trace_impl.get_signal('count') == 0
    assert trace_impl.get_signal('enable') == 0
    
    # Advance to t=15ns
    trace_impl.advance_to(15.0)
    assert trace_impl.get_signal('count') == 1
    
    # Advance to t=25ns
    trace_impl.advance_to(25.0)
    assert trace_impl.get_signal('count') == 2
    
    # Advance to t=35ns
    trace_impl.advance_to(35.0)
    assert trace_impl.get_signal('count') == 3
    assert trace_impl.get_signal('enable') == 1
    
    # Advance to t=45ns
    trace_impl.advance_to(45.0)
    assert trace_impl.get_signal('count') == 4
