"""VCD (Value Change Dump) file reader for trace playback.

Parses IEEE 1800-2017 VCD files and provides access to signal hierarchy
and value changes for replay.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import re


@dataclass
class VCDSignal:
    """Metadata for a signal in the VCD file."""
    identifier: str  # VCD identifier code (e.g., "!", "#")
    name: str  # Signal name
    width: int  # Bit width
    scope_path: str  # Hierarchical path (e.g., "top.child")
    var_type: str  # Wire, reg, integer, etc.


@dataclass
class VCDValueChange:
    """A value change event in the VCD file."""
    time_ns: float  # Time in nanoseconds
    identifier: str  # VCD identifier
    value: int  # New value


@dataclass
class VCDData:
    """Parsed VCD file data."""
    timescale_ns: float = 1.0  # Timescale in nanoseconds
    signals: Dict[str, VCDSignal] = field(default_factory=dict)  # identifier -> signal
    signals_by_path: Dict[str, VCDSignal] = field(default_factory=dict)  # full_path -> signal
    value_changes: List[VCDValueChange] = field(default_factory=list)
    initial_values: Dict[str, int] = field(default_factory=dict)  # identifier -> initial value


class VCDReader:
    """Parser for VCD files.
    
    Reads VCD file and extracts signal hierarchy and value changes.
    """
    
    def __init__(self, filename: str):
        self._filename = filename
        self._data = VCDData()
        self._current_scope: List[str] = []
        self._id_to_signal: Dict[str, VCDSignal] = {}
    
    def parse(self) -> VCDData:
        """Parse the VCD file and return parsed data."""
        with open(self._filename, 'r') as f:
            content = f.read()
        
        # Parse in two phases: header, then simulation data
        lines = content.split('\n')
        line_idx = 0
        
        # Phase 1: Parse header (before $enddefinitions)
        while line_idx < len(lines):
            line = lines[line_idx].strip()
            
            if line.startswith('$enddefinitions'):
                line_idx += 1
                break
            
            if line.startswith('$timescale'):
                self._parse_timescale(lines, line_idx)
            elif line.startswith('$scope'):
                self._parse_scope(line)
            elif line.startswith('$upscope'):
                if self._current_scope:
                    self._current_scope.pop()
            elif line.startswith('$var'):
                self._parse_var(line)
            
            line_idx += 1
        
        # Phase 2: Parse simulation data
        current_time_ns = 0.0
        in_dumpvars = False
        
        while line_idx < len(lines):
            line = lines[line_idx].strip()
            
            if not line:
                line_idx += 1
                continue
            
            if line.startswith('$dumpvars'):
                in_dumpvars = True
                line_idx += 1
                continue
            
            if line.startswith('$end'):
                in_dumpvars = False
                line_idx += 1
                continue
            
            if line.startswith('$dumpoff') or line.startswith('$dumpon') or line.startswith('$dumpall'):
                # Skip dump control commands
                line_idx += 1
                continue
            
            if line.startswith('$comment'):
                # Skip comments until $end
                while line_idx < len(lines) and '$end' not in lines[line_idx]:
                    line_idx += 1
                line_idx += 1
                continue
            
            if line.startswith('#'):
                # Timestamp
                try:
                    time_units = int(line[1:])
                    current_time_ns = time_units * self._data.timescale_ns
                except ValueError:
                    pass
                line_idx += 1
                continue
            
            # Value change
            value, identifier = self._parse_value_change(line)
            if identifier is not None:
                if in_dumpvars:
                    # Initial values
                    self._data.initial_values[identifier] = value
                else:
                    # Regular value change
                    self._data.value_changes.append(VCDValueChange(
                        time_ns=current_time_ns,
                        identifier=identifier,
                        value=value
                    ))
            
            line_idx += 1
        
        return self._data
    
    def _parse_timescale(self, lines: List[str], start_idx: int):
        """Parse $timescale section."""
        # Find the timescale value
        text = ""
        for i in range(start_idx, min(start_idx + 5, len(lines))):
            text += " " + lines[i]
            if '$end' in lines[i]:
                break
        
        # Extract number and unit
        # Format: $timescale <number> <unit> $end
        match = re.search(r'\$timescale\s+(\d+)\s*(s|ms|us|ns|ps|fs)', text.lower())
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            
            unit_to_ns = {
                's': 1e9,
                'ms': 1e6,
                'us': 1e3,
                'ns': 1.0,
                'ps': 1e-3,
                'fs': 1e-6,
            }
            self._data.timescale_ns = value * unit_to_ns.get(unit, 1.0)
    
    def _parse_scope(self, line: str):
        """Parse $scope line."""
        # Format: $scope <type> <name> $end
        match = re.match(r'\$scope\s+\w+\s+(\S+)\s*\$end', line)
        if match:
            self._current_scope.append(match.group(1))
    
    def _parse_var(self, line: str):
        """Parse $var line."""
        # Format: $var <type> <width> <identifier> <name> $end
        # or: $var <type> <width> <identifier> <name>[<index>] $end
        match = re.match(r'\$var\s+(\w+)\s+(\d+)\s+(\S+)\s+(\S+)(?:\s*\[\d+(?::\d+)?\])?\s*\$end', line)
        if match:
            var_type = match.group(1)
            width = int(match.group(2))
            identifier = match.group(3)
            name = match.group(4)
            
            scope_path = '.'.join(self._current_scope) if self._current_scope else ""
            full_path = f"{scope_path}.{name}" if scope_path else name
            
            signal = VCDSignal(
                identifier=identifier,
                name=name,
                width=width,
                scope_path=scope_path,
                var_type=var_type
            )
            
            self._data.signals[identifier] = signal
            self._data.signals_by_path[full_path] = signal
            self._id_to_signal[identifier] = signal
    
    def _parse_value_change(self, line: str) -> Tuple[int, Optional[str]]:
        """Parse a value change line.
        
        Returns (value, identifier) or (0, None) if parsing fails.
        """
        line = line.strip()
        
        if not line:
            return 0, None
        
        # Binary vector: b<binary> <identifier>
        if line.startswith('b') or line.startswith('B'):
            parts = line[1:].split()
            if len(parts) >= 2:
                bin_str = parts[0]
                identifier = parts[1]
                value = self._parse_binary(bin_str)
                return value, identifier
        
        # Real number: r<real> <identifier>
        elif line.startswith('r') or line.startswith('R'):
            parts = line[1:].split()
            if len(parts) >= 2:
                try:
                    value = int(float(parts[0]))
                except ValueError:
                    value = 0
                identifier = parts[1]
                return value, identifier
        
        # Scalar: <value><identifier> (no space)
        elif line[0] in '01xXzZ':
            value_char = line[0].lower()
            identifier = line[1:]
            
            if value_char == '0':
                return 0, identifier
            elif value_char == '1':
                return 1, identifier
            else:  # x or z
                return 0, identifier  # Treat as 0 for simplicity
        
        return 0, None
    
    def _parse_binary(self, bin_str: str) -> int:
        """Parse binary string, handling x and z as 0."""
        result = 0
        for char in bin_str:
            result <<= 1
            if char == '1':
                result |= 1
            # x, z, 0 all result in 0
        return result
