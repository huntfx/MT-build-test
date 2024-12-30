import math
import multiprocessing
import traceback

import numpy as np
from scipy import ndimage

from mousetracks.image import colours
from .. import ipc
from ...utils.math import calculate_line, calculate_distance
from ...utils.win import cursor_position, monitor_locations


COMPRESSION_FACTOR = 1.1

COMPRESSION_THRESHOLD = 425000  # Max: 2 ** 64 - 1


def gaussian_size(width, height, multiplier: float = 1.0, base: float = 0.0125):
    """Calculate size of gaussian blur."""
    return int(round(min(width, height) * base * multiplier))


def range_array(array: np.ndarray) -> np.ndarray:
    unique_values, mapped_array = np.unique(array, return_inverse=True)
    return mapped_array


class ExitRequest(Exception):
    """Custom exception to raise and catch when an exit is requested."""


class PixelArray(dict):
    def __missing__(self, key: tuple[int, int]) -> np.ndarray:
        self[key] = value = np.zeros((key[1], key[0]), dtype=np.uint8)
        return value

    def set_value(self, key: tuple[int, int], index: tuple[int, int], value: int) -> None:
        """Set a value in an array, updating the type if required."""
        array = self[key]

        if value >= np.iinfo(array.dtype).max:
            for dtype in (np.uint16, np.uint32, np.uint64):
                if value < np.iinfo(dtype).max:
                    self[key] = array = array.astype(dtype)
                    break

        array[index] = value


def array_rescale(array: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
    """Rescale the array with the correct filtering."""
    input_height, input_width = array.shape

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


class Processing:
    def __init__(self, q_send: multiprocessing.Queue, q_receive: multiprocessing.Queue) -> None:
        self.q_send = q_send
        self.q_receive = q_receive

        self.mouse_track_maps = PixelArray()
        self.mouse_speed_maps = PixelArray()
        self.mouse_single_clicks = PixelArray()
        self.mouse_double_clicks = PixelArray()
        self.mouse_move_count = 0

        self.mouse_distance = 0.0
        self.mouse_position = cursor_position()
        self.mouse_move_tick = 0
        self.monitor_data = monitor_locations()
        self.previous_monitor = None
        self.pause_tick = 0
        self.state = ipc.TrackingState.State.Pause

    def _monitor_offset(self, pixel: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
        """Detect which monitor the pixel is on."""
        for x1, y1, x2, y2 in self.monitor_data:
            if x1 <= pixel[0] < x2 and y1 <= pixel[1] < y2:
                return ((x2 - x1, y2 - y1), (x1, y1))
        raise ValueError(f'coordinate {pixel} not in monitors')

    def _cursor_move(self, message: ipc.MouseMove) -> None:
        """Handle a mouse move message.

        There are some caveats that are hard to handle. If a mouse is
        programmatically moved, then it will jump to a location on the
        screen. A check can be done to skip drawing if the cursor wasn't
        previously moving, but the first frame of movement wil also
        always get skipped. Detecting the vector of movement was tried,
        but it was too overcomplicated and wasn't good enough.

        There's never been an issue with the original script, so the
        behaviour has been copied.
        - Time tracks are fully recorded, and will capture jumps.
        This is fine as those tracks will be buried over time.
        - Speed tracks are only recorded if the cursor was previously
        moving, the downside being it will still record any jumps while
        moving, and will always skip the first frame of movement.
        """
        print(f'[Processing] Mouse has moved to {message.position}')

        # If the ticks match then overwrite the old data
        if message.tick == self.mouse_move_tick:
            self.mouse_position = message.position

        distance = calculate_distance(message.position, self.mouse_position)
        moving = message.tick == self.mouse_move_tick + 1

        # Calculate the data
        pixels = calculate_line(message.position, self.mouse_position)
        if distance:
            self.mouse_distance += distance

        # Add the pixels to an array
        for pixel in pixels:
            current_monitor, offset = self._monitor_offset(pixel)
            index = (pixel[1] - offset[1], pixel[0] - offset[0])

            self.mouse_track_maps.set_value(current_monitor, index, self.mouse_move_count)
            if distance and moving:
                self.mouse_speed_maps.set_value(current_monitor, index, max(self.mouse_speed_maps[current_monitor][index], int(100 * distance)))

        # Update the saved data
        self.mouse_move_count += 1
        self.mouse_position = message.position
        self.mouse_move_tick = message.tick

        # Check if array compression is required
        # This is important for the time maps
        # For speed, it just helps flatten out values that are too large
        if self.mouse_move_count > COMPRESSION_THRESHOLD:
            print(f'[Processing] Tracking threshold reached, reducing values...')
            for maps in (self.mouse_track_maps, self.mouse_speed_maps):
                for res, array in maps.items():
                    maps[res] = (array / COMPRESSION_FACTOR).astype(array.dtype)
            print(f'[Processing] Reduced all arrays')

    def _process_message(self, message: ipc.Message) -> None:
        """Process an item of data."""
        match message:
            case ipc.RenderRequest(width=width, height=height,
                                   type=ipc.RenderType.Time | ipc.RenderType.TimeSincePause | ipc.RenderType.Speed):
                print('[Processing] Render request received...')
                # Choose the data to work on
                maps: dict[tuple[int, int], np.ndarray]
                match message.type:
                    case ipc.RenderType.Time:
                        maps = self.mouse_track_maps

                    # Subtract a value from each array and ensure it doesn't go below 0
                    case ipc.RenderType.TimeSincePause:
                        maps = {}
                        for res, array in self.mouse_track_maps.items():
                            partial_array = array.astype(np.int64) - self.pause_tick
                            partial_array[partial_array < 0] = 0
                            maps[res] = partial_array

                    case ipc.RenderType.Speed:
                        maps = self.mouse_speed_maps

                    case _:
                        raise NotImplementedError(message.type)

                # Find the largest most common resolution
                if width is None or height is None:
                    popularity = {}
                    for res, array in maps.items():
                        popularity[res] = np.sum(array > 0)
                    threshold = max(popularity.values()) * 0.9
                    _width, _height = max(res for res, value in popularity.items() if value > threshold)

                    if width is None and height is None:
                        width = _width
                        height = _height
                    else:
                        aspect = _width / _height
                        if width is None:
                            width = int(height * aspect)
                        if height is None:
                            height = int(width / aspect)

                # Apply the sampling amount
                scale_width = int(width * message.sampling)
                scale_height = int(height * message.sampling)

                # Downscale and normalise values to 0-255
                normalised_arrays = []
                for array in maps.values():
                    scaled_array = array_rescale(array, scale_width, scale_height)
                    max_time = np.max(scaled_array) or 1
                    normalised_arrays.append((scaled_array.astype(np.float64) * (255 / max_time)).astype(np.uint8))

                # Combine the arrays using the maximum values of each
                if normalised_arrays:
                    combined_array = np.maximum.reduce(normalised_arrays)
                else:
                    combined_array = np.zeros((scale_height, scale_width), dtype=np.int8)

                # Map to a colour lookup table
                colour_lookup = generate_colour_lookup(*colours.calculate_colour_map(message.colour_map))
                coloured_array = colour_lookup[combined_array]

                self.q_send.put(ipc.Render(message.type, coloured_array, message.sampling, self.mouse_move_tick))
                print('[Processing] Render request completed')

            case ipc.RenderRequest(width=width, height=height,
                                   type=ipc.RenderType.SingleClick | ipc.RenderType.DoubleClick):

                maps: dict[tuple[int, int], np.ndarray]
                match message.type:
                    case ipc.RenderType.SingleClick:
                        maps = self.mouse_single_clicks
                    case ipc.RenderType.DoubleClick:
                        maps = self.mouse_double_clicks
                    case _:
                        raise NotImplementedError(message.type)

                # Find the largest most common resolution
                if width is None or height is None:
                    popularity = {}
                    for res, array in maps.items():
                        popularity[res] = np.sum(array > 0)
                    threshold = max(popularity.values()) * 0.9
                    _width, _height = max(res for res, value in popularity.items() if value > threshold)

                    if width is None and height is None:
                        width = _width
                        height = _height
                    else:
                        aspect = _width / _height
                        if width is None:
                            width = int(height * aspect)
                        if height is None:
                            height = int(width / aspect)

                # Apply the sampling amount
                scale_width = int(width * message.sampling)
                scale_height = int(height * message.sampling)

                # Scale all arrays to the same size
                rescaled_arrays = []
                for array in maps.values():
                    rescaled_arrays.append(array_rescale(array, scale_width, scale_height))

                if rescaled_arrays:
                    combined_array = np.add.reduce(rescaled_arrays, dtype=np.uint64)
                else:
                    combined_array = np.zeros((scale_height, scale_width), dtype=np.int8)

                # Convert the array to a linear array
                unique_values, unique_indexes = np.unique(combined_array, return_inverse=True)

                # Apply a gaussian blur on the raw data
                blur_amount = gaussian_size(scale_width, scale_height)
                heatmap = ndimage.gaussian_filter(unique_indexes.astype(np.float64), sigma=blur_amount)

                # Convert the array to 0-255
                max_value = np.max(heatmap) or 1
                normalised = (heatmap.astype(np.float64) * (255 / max_value)).astype(np.uint8)

                # Map to a colour lookup table
                colour_lookup = generate_colour_lookup(*colours.calculate_colour_map(message.colour_map))
                coloured_array = colour_lookup[normalised]

                # TODO: Reimplement the heatmap range clipping
                # will be easier to test once a heavier heatmap can be used
                # min_value = np.min(heatmap)
                # all_values = np.sort(heatmap.ravel(), unique=True)
                # max_value = all_values[int(round(len(unique_values) * 0.005))]

                self.q_send.put(ipc.Render(message.type, coloured_array, message.sampling, self.mouse_move_tick))
                print('[Processing] Render request completed')

            case ipc.MouseMove():
                self._cursor_move(message)

            case ipc.MouseClick():
                if message.double:
                    arrays = self.mouse_double_clicks
                    print(f'[Processing] Mouse button {message.button} double clicked.')
                else:
                    arrays = self.mouse_single_clicks
                    print(f'[Processing] Mouse button {message.button} clicked.')

                current_monitor, offset = self._monitor_offset(message.position)
                index = (message.position[1] - offset[1], message.position[0] - offset[0])
                arrays.set_value(current_monitor, index, int(arrays[current_monitor][index]) + 1)

            case ipc.MonitorsChanged():
                print(f'[Processing] Monitors changed.')
                self.monitor_data = message.data

            case ipc.DebugRaiseError():
                raise RuntimeError('test exception')

            case ipc.TrackingState():
                match message.state:
                    case ipc.TrackingState.State.Stop:
                        raise ExitRequest
                    case ipc.TrackingState.State.Pause:
                        self.pause_tick = self.mouse_move_count
                self.state = message.state

            case _:
                raise NotImplementedError(message)

    def run(self) -> None:
        print('[Processing] Loaded.')

        try:
            while True:
                self._process_message(self.q_receive.get())

        except ExitRequest:
            print('[Processing] Shut down.')

        # Catch error after KeyboardInterrupt
        except EOFError:
            print('[Processing] Force shut down.')
            return

        except Exception as e:
            self.q_send.put(ipc.Traceback(e, traceback.format_exc()))
            print('[Processing] Error shut down.')


def run(q_send: multiprocessing.Queue, q_receive: multiprocessing.Queue) -> None:
    Processing(q_send, q_receive).run()
