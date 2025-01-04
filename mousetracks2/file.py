import os
import pickle
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator, Optional, Self

import numpy as np

from .utils.win import MOUSE_BUTTONS


COMPRESSION_FACTOR = 1.1

COMPRESSION_THRESHOLD = 425000  # Max: 2 ** 64 - 1

EXPECTED_LEGACY_VERSION = 34
"""Only mtk files of this version are allowed to be loaded.
Any other versions must be upgraded with the mousetracks v1 script.
"""

CURRENT_FILE_VERSION = 1


class InvalidVersionError(Exception):
    def __init__(self, filename: str, version: int, expected: int) -> None:
        super().__init__(f'The {os.path.basename(filename)} version is invalid '
                         f'(got: v{version}, expected: v{expected})')


class TrackingArray:
    """Create an integer array and update the dtype when required.
    This is for memory optimisation as the arrays are large.

    Ideally this would inherit `np.ndarray`, but changing the dtype of
    an array in-place isn't supported.
    """

    DTYPES = [np.uint16, np.uint32, np.uint64]
    MAX_VALUES = [np.iinfo(dtype).max for dtype in DTYPES]

    def __init__(self, shape: int | list[int] | np.ndarray) -> None:
        if isinstance(shape, np.ndarray):
            self.array = shape
        else:
            self.array = np.zeros(shape, dtype=np.uint8)
        self.max_value = np.iinfo(np.uint8).max

    def __array__(self) -> np.ndarray:
        """For internal numpy usage."""
        return self.array

    def __str__(self) -> str:
        return str(self.array)

    def __repr__(self) -> str:
        return repr(self.array)

    def __getitem__(self, item: any) -> int:
        """Get an array item."""
        return self.array[item]

    def __setitem__(self, item: any, value: int) -> None:
        """Set an array item, changing dtype if required."""
        if value >= self.max_value:
            for dtype, max_value in zip(self.DTYPES, self.MAX_VALUES):
                if value < max_value:
                    self.max_value = max_value
                    self.array = self.array.astype(dtype)
                    break

        self.array[item] = value

    def _write_to_zip(self, zf: zipfile.ZipFile, path: str) -> None:
        with zf.open(path, 'w') as f:
            np.save(f, self)

    @classmethod
    def _load_from_zip(cls, zf: zipfile.ZipFile, path: str) -> Self:
        with zf.open(path, 'r') as f:
            return cls(np.load(f))


class ArrayResolutionMap(dict):
    """Store multiple arrays for different resolutions.
    New arrays will be created on demand.
    """

    def __missing__(self, key: tuple[int, int]) -> TrackingArray:
        self[key] = TrackingArray([key[1], key[0]])
        return self[key]

    def __setitem__(self, key: tuple[int, int], array: np.ndarray | TrackingArray) -> None:
        if isinstance(array, np.ndarray):
            self[key].array = array
        else:
            super().__setitem__(key, array)

    def _write_to_zip(self, zf: zipfile.ZipFile, subfolder: str) -> None:
        for (width, height), array in self.items():
            array._write_to_zip(zf, f'{subfolder}/{width}x{height}.npy')

    def _load_from_zip(self, zf: zipfile.ZipFile, subfolder: str):
        relative_paths = [path[len(subfolder):].lstrip('/') for path in zf.namelist() if path.startswith(subfolder)]

        for relative_path in relative_paths:
            match = re.match(r'(\d)+x(\d)+\.npy', relative_path)
            if not match:
                raise RuntimeError(f'unexpected data in file: {subfolder}/{relative_path}')
            width, height = map(int, match.groups())
            self[(width, height)] = TrackingArray._load_from_zip(zf, f'{subfolder}/{relative_path}')


@dataclass
class MovementMaps:
    """Hold the data for the line based maps."""

    _MAX_VALUE = 2 ** 64 - 1

    position: Optional[tuple[int, int]] = field(default=None)  # TODO: Don't store here
    sequential_arrays: ArrayResolutionMap = field(default_factory=ArrayResolutionMap)
    density_arrays: ArrayResolutionMap = field(default_factory=ArrayResolutionMap)
    speed_arrays: ArrayResolutionMap = field(default_factory=ArrayResolutionMap)
    distance: float = field(default=0.0)
    counter: int = field(default=0)
    ticks: int = field(default=0)
    tick: int = field(default=0)  # TODO: Don't store here

    def requires_compression(self, threshold: int = COMPRESSION_THRESHOLD) -> bool:
        """Check if compression is required."""
        return self.counter > min(threshold, self._MAX_VALUE)

    def run_compression(self, factor: float = COMPRESSION_FACTOR) -> None:
        """Compress down the values.
        This is important for the time arrays, but helps flatten out
        speed values that are too large.
        """
        for maps in (self.sequential_arrays, self.speed_arrays):
            for res, array in maps.items():
                maps[res] = (array / factor).astype(array.dtype)
            self.counter = int(self.counter // factor)

    def _iter_array_types(self) -> Iterator[tuple[str, ArrayResolutionMap]]:
        yield 'sequential', self.sequential_arrays
        yield 'density', self.density_arrays
        yield 'speed', self.speed_arrays

    def _write_to_zip(self, zf: zipfile.ZipFile, subfolder: str) -> None:
        for array_type, array_resolution_map in self._iter_array_types():
            array_resolution_map._write_to_zip(zf, f'{subfolder}/{array_type}')
        zf.writestr(f'{subfolder}/distance', str(self.distance))
        zf.writestr(f'{subfolder}/counter', str(self.counter))
        zf.writestr(f'{subfolder}/ticks', str(self.counter))

    def _load_from_zip(self, zf: zipfile.ZipFile, subfolder: str):
        folders = {path[len(subfolder):].lstrip('/').split('/', 1)[0]
                   for path in zf.namelist() if path.startswith(subfolder)}

        for folder in folders:
            path = f'{subfolder}/{folder}'
            if folder == 'distance':
                self.distance = float(zf.read(path))
            elif folder == 'counter':
                self.counter = int(zf.read(path))
            elif folder == 'ticks':
                self.ticks = int(zf.read(path))
            elif folder == 'sequential':
                self.sequential_arrays._load_from_zip(zf, path)
            elif folder == 'density':
                self.density_arrays._load_from_zip(zf, path)
            elif folder == 'speed':
                self.speed_arrays._load_from_zip(zf, path)


@dataclass
class ApplicationData:
    """The data stored per application."""

    cursor_map: MovementMaps = field(default_factory=MovementMaps)
    thumbstick_l_map: dict[int, MovementMaps] = field(default_factory=lambda: defaultdict(MovementMaps))
    thumbstick_r_map: dict[int, MovementMaps] = field(default_factory=lambda: defaultdict(MovementMaps))
    trigger_map: dict[int, MovementMaps] = field(default_factory=lambda: defaultdict(MovementMaps))

    mouse_single_clicks: dict[int, ArrayResolutionMap] = field(default_factory=lambda: defaultdict(ArrayResolutionMap))
    mouse_double_clicks: dict[int, ArrayResolutionMap] = field(default_factory=lambda: defaultdict(ArrayResolutionMap))
    mouse_held_clicks: dict[int, ArrayResolutionMap] = field(default_factory=lambda: defaultdict(ArrayResolutionMap))

    key_presses: TrackingArray = field(default_factory=lambda: TrackingArray(0xFF))
    key_held: TrackingArray = field(default_factory=lambda: TrackingArray(0xFF))

    button_presses: dict[int, TrackingArray] = field(default_factory=lambda: defaultdict(lambda: TrackingArray(20)))
    button_held: dict[int, TrackingArray] = field(default_factory=lambda: defaultdict(lambda: TrackingArray(20)))

    def save(self, path: str):
        with zipfile.ZipFile(path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:

            zf.writestr('version', str(CURRENT_FILE_VERSION))

            self.cursor_map._write_to_zip(zf, 'data/mouse/cursor')
            for i, array_resolution_map in self.mouse_single_clicks.items():
                array_resolution_map._write_to_zip(zf, f'data/mouse/clicks/{i}/single')
            for i, array_resolution_map in self.mouse_double_clicks.items():
                array_resolution_map._write_to_zip(zf, f'data/mouse/clicks/{i}/double')
            for i, array_resolution_map in self.mouse_held_clicks.items():
                array_resolution_map._write_to_zip(zf, f'data/mouse/clicks/{i}/held')

            self.key_presses._write_to_zip(zf, 'data/keyboard/pressed.npy')
            self.key_held._write_to_zip(zf, 'data/keyboard/held.npy')

            for i, array in self.button_presses.items():
                array._write_to_zip(zf, f'data/gamepad/{i}/pressed.npy')
            for i, array in self.button_held.items():
                array._write_to_zip(zf, f'data/gamepad/{i}/held.npy')
            for i, array_map in self.thumbstick_l_map.items():
                array_map._write_to_zip(zf, f'data/gamepad/{i}/left_stick')
            for i, array_map in self.thumbstick_r_map.items():
                array_map._write_to_zip(zf, f'data/gamepad/{i}/right_stick')

    @classmethod
    def load(cls, path: str):
        new = cls()
        with zipfile.ZipFile(path, mode='r') as zf:
            version = int(zf.read('version'))
            if version != CURRENT_FILE_VERSION:
                raise RuntimeError(f'unexpected version: {version}')

            all_paths = zf.namelist()

            new.cursor_map._load_from_zip(zf, 'data/mouse/cursor')
            mouse_buttons = {int(path.split('/')[3]) for path in all_paths if path.startswith('data/mouse/clicks')}
            for i in mouse_buttons:
                new.mouse_single_clicks[i]._load_from_zip(zf, f'data/mouse/clicks/{i}/single')
                new.mouse_double_clicks[i]._load_from_zip(zf, f'data/mouse/clicks/{i}/double')
                new.mouse_held_clicks[i]._load_from_zip(zf, f'data/mouse/clicks/{i}/held')

            new.key_presses._load_from_zip(zf, f'data/keyboard/pressed.npy')
            new.key_held._load_from_zip(zf, f'data/keyboard/held.npy')

            gamepad_indexes = {int(path.split('/')[2]) for path in all_paths if path.startswith('data/gamepad')}
            for i in gamepad_indexes:
                new.thumbstick_l_map[i]._load_from_zip(zf, f'data/gamepad/{i}/left_stick')
                new.thumbstick_r_map[i]._load_from_zip(zf, f'data/gamepad/{i}/right_stick')
                new.button_presses[i]._load_from_zip(zf, f'data/gamepad/{i}/pressed.npy')
                new.button_held[i]._load_from_zip(zf, f'data/gamepad/{i}/held.npy')

        return new


def load_legacy_data(path: str) -> ApplicationData:
    """Load in data from the legacy tracking.

    Mouse:
        Time, speed and click arrays are imported.
        There is no density data.

    Keyboard:
        Everything is imported.

    Gamepad:
        Mostly imported.
        Trigger presses may be lost.
        Thumbstick data is discarded as X and Y were recorded separately
        and cannot be recombined.
    """

    result = ApplicationData()

    with zipfile.ZipFile(path, 'r') as zf:
        # Check the version
        version = int(zf.read('metadata/file.txt'))
        if version != EXPECTED_LEGACY_VERSION:
            raise InvalidVersionError(path, version, EXPECTED_LEGACY_VERSION)

        # Load in the data
        with zf.open('data.pkl') as f:
            data: dict[str, any] = pickle.load(f)

        # Load in the metadata
        # created: int = int(data['Time']['Created'])
        # modified: int = int(data['Time']['Modified'])
        # session_count: int = data['TimesLoaded']
        # ticks_total: int = data['Ticks']['Total']
        # ticks_recorded: int = data['Ticks']['Recorded']

        result.cursor_map.distance = int(data['Distance']['Tracks'])
        result.cursor_map.sequence = int(data['Ticks']['Tracks'])

        # Process main tracking data
        for resolution, values in data['Resolution'].items():
            # Load tracking heatmap
            for array_type, container in (('Tracks', result.cursor_map.sequential_arrays), ('Speed', result.cursor_map.speed_arrays)):
                with zf.open(f'maps/{values[array_type]}.npy') as f:
                    array = np.load(f)
                    if np.any(array > 0):
                        container[resolution] = array

            # Load click heatmap
            for array_type, container in (('Single', result.mouse_single_clicks), ('Double', result.mouse_double_clicks)):
                for i, mb in enumerate(('Left', 'Middle', 'Right')):
                    with zf.open(f'maps/{values["Clicks"][array_type][mb]}.npy') as f:
                        array = np.load(f)
                        if np.any(array > 0):
                            container[MOUSE_BUTTONS[i]][resolution] = array

        # Process key/button data
        for opcode, count in data['Keys']['All']['Pressed'].items():
            result.key_presses[opcode] = count
        for opcode, count in data['Keys']['All']['Held'].items():
            result.key_held[opcode] = count

        for opcode, count in data['Gamepad']['All']['Buttons']['Pressed'].items():
            result.button_presses[opcode] = count
        for opcode, count in data['Gamepad']['All']['Buttons']['Held'].items():
            result.button_held[opcode] = count

        return result
