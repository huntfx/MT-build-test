import math
from collections import defaultdict
from typing import Optional

import numpy as np
from scipy import ndimage

from mousetracks.image import colours
from .typing import ArrayLike


class EmptyRenderError(ValueError):
    """Raise when a render is requested with not enough data.

    For example if only height is given with no arrays, then it's not
    possible to calculate the correct width and render an empty image.
    """

    def __init__(self) -> None:
        super().__init__('input arrays cannot be empty if size not defined')


def array_target_resolution(arrays: list[ArrayLike], width: Optional[int] = None,
                            height: Optional[int] = None) -> tuple[int, int]:
    """Calculate a target resolution.
    If width or height is given, then it will be used.
    The aspect ratio is taken into consideration.
    """
    if width is not None and height is not None:
        return width, height

    popularity = defaultdict(int)
    for array in map(np.asarray, arrays):
        res_y, res_x = array.shape
        popularity[(res_x, res_y)] += np.sum(np.greater(array, 0))
    threshold = max(popularity.values()) * 0.9
    _width, _height = max(res for res, value in popularity.items() if value > threshold)

    if width is None and height is None:
        return _width, _height

    aspect = _width / _height
    if width is None:
        return int(height * aspect), height
    return width, int(width / aspect)


def array_to_uint8(array: np.ndarray) -> np.ndarray:
    """Normalise an array to map it's values from 0-255."""
    max_value = np.max(array)
    if not max_value:
        return np.zeros(array.shape, dtype=np.uint8)
    return (array.astype(np.float64) * (255 / max_value)).astype(np.uint8)


def gaussian_size(width, height, multiplier: float = 1.0, base: float = 0.0125):
    """Choose a gaussian blur amount to use for a given resolution."""
    return int(round(min(width, height) * base * multiplier))


def array_rescale(array: ArrayLike, target_width: int, target_height: int) -> np.ndarray:
    """Rescale the array with the correct filtering."""
    input_height, input_width = np.shape(array)

    # No rescaling required
    if target_height == input_height and target_width == input_width:
        return array

    # Upscale without blurring
    if target_height > input_height or target_width > input_width:
        zoom_factor = (target_height / input_height, target_width / input_width)
        return ndimage.zoom(array, zoom_factor, order=0)

    # Downscale without losing detail (credit to ChatGPT)
    block_height = input_height / target_height
    block_width = input_width / target_width
    pooled_full = ndimage.maximum_filter(array, size=(int(math.ceil(block_height)), int(math.ceil(block_width))))

    indices_y = np.linspace(0, input_height - 1, target_height).astype(np.uint64)
    indices_x = np.linspace(0, input_width - 1, target_width).astype(np.uint64)
    return np.ascontiguousarray(pooled_full[indices_y][:, indices_x])


def generate_colour_lookup(*colours: tuple[int, int, int, int], steps: int = 256) -> np.ndarray:
    """Generate a color lookup table transitioning smoothly between given colors."""
    lookup = np.zeros((steps, 4), dtype=np.uint8)

    # Fix for single inputs
    if len(colours) == 1:
        colours = (colours[0], colours[0])

    num_transitions = len(colours) - 1
    steps_per_transition = steps // num_transitions
    remaining_steps = steps % num_transitions  # Distribute extra steps evenly

    start_index = 0
    for i in range(num_transitions):
        # Determine start and end colors for the current transition
        start_color = np.array(colours[i])
        end_color = np.array(colours[i + 1])

        # Adjust steps for the last transition to include any remaining steps
        current_steps = steps_per_transition + (i < remaining_steps)

        # Linearly interpolate between start_color and end_color
        for j in range(current_steps):
            t = j / (current_steps - 1)  # Normalized position (0 to 1)
            lookup[start_index + j] = (1 - t) * start_color + t * end_color

        start_index += current_steps

    return lookup


def render(colour_map: str, positional_arrays: dict[tuple[int, int], list[ArrayLike]],
           width: Optional[int] = None, height: Optional[int] = None, sampling: int = 1,
           linear: bool = False, blur: bool = False) -> np.ndarray:
    """Combine a group of arrays into a single array for rendering.

    Parameters:
        colour_map: Must be either a predefined or manually defined map.
            See `config/colours.txt` for examples.
        positional_arrays: Dict of draw position and list of arrays.
            For now it only supports (0, 0) and (0, 1) for left and
            right.
        width: Force a particular width.
            If not set, the aspect ratio will be used to calculate it.
        height: Force a particular height.
            If not set, the aspect ratio will be used to calculate it.
        sampling: How many pixels to calculate per pixel.
            Setting this value to 2 will upscale everything to twice the
            target resolution.
            It ensures a more accurate representation when combining
            different resolutions together.
        linear: Remap the array to linear values.
            This will ensure a smooth gradient.
        blur: Blur the array, for example for a heatmap.
    """
    # Calculate width / height
    all_arrays = []
    for arrays in positional_arrays.values():
        all_arrays.extend(arrays)
    if all_arrays:
        width, height = array_target_resolution(all_arrays, width, height)
    elif width is None or height is None:
        raise EmptyRenderError

    scale_width = width * sampling
    scale_height = height * sampling
    blur_amount = gaussian_size(scale_width, scale_height)

    # Rescale the arrays to the target size and combine them
    combined_arrays: dict[tuple[int, int], np.ndarray] = {}
    for pos, arrays in positional_arrays.items():
        if not arrays:
            continue
        rescaled = [array_rescale(array, scale_width, scale_height) for array in arrays]
        combined_arrays[pos] = np.maximum.reduce(rescaled)

    # Convert to linear arrays
    if linear:
        combined_arrays = {pos: np.unique(array, return_inverse=True)[1]
                           for pos, array in combined_arrays.items()}

    # Apply gaussian blur
    if blur:
        combined_arrays = {pos: ndimage.gaussian_filter(array.astype(np.float64), sigma=blur_amount)
                           for pos, array in combined_arrays.items()}

        # TODO: Reimplement the heatmap range clipping
        # It will be easier to test once saving works and a heavier heatmap can be used
        # min_value = np.min(heatmap)
        # all_values = np.sort(heatmap.ravel(), unique=True)
        # max_value = all_values[int(round(len(unique_values) * 0.005))]

    # Equalise the max values
    if len(combined_arrays) > 1:
        max_values = {pos: np.max(array) for pos, array in combined_arrays.items()}
        max_value = max(max_values.values())
        combined_arrays = {pos: combined_arrays[pos].astype(np.float64) * (max_value / value)
                           for pos, value in max_values.items()}

    # Combine all positional arrays into one big array
    combined_array = combine_array_grid(combined_arrays, scale_width, scale_height)

    # Convert the array to 0-255 and map to a colour lookup table
    try:
        colour_map_data = colours.calculate_colour_map(colour_map)
    except Exception:  # Old code - just fallback to tranparent
        colour_map_data = [(0, 0, 0, 0)]

    colour_lookup = generate_colour_lookup(*colour_map_data)
    return colour_lookup[array_to_uint8(combined_array)]


def combine_array_grid(positional_arrays: dict[tuple[int, int], np.ndarray],
                       scale_width: float, scale_height: float) -> np.ndarray:
    """Combine arrays based on their positions and offsets."""
    if not positional_arrays:
        return np.zeros((scale_height, scale_width), dtype=np.int8)

    if len(set(array.shape for array in positional_arrays.values())) != 1:
        raise ValueError('all arrays must be the same size')

    # Determine the total required size
    min_col = min(pos[0] for pos in positional_arrays)
    max_col = max(pos[0] for pos in positional_arrays)
    min_row = min(pos[1] for pos in positional_arrays)
    max_row = max(pos[1] for pos in positional_arrays)
    total_width = scale_width * (max_col - min_col + 1)
    total_height = scale_height * (max_row - min_row + 1)

    # Create the combined array
    combined_array = np.zeros((total_height, total_width), dtype=np.float64)
    for (col, row), array in positional_arrays.items():
        x = col * scale_width
        y = row * scale_height
        combined_array[y: y + scale_height, x:x + scale_width] = array

    return combined_array
