import time
from itertools import count

from . import utils
from .. import ipc
from ...utils.win import cursor_position, monitor_locations, get_mouse_click


UPDATES_PER_SECOND = 60

DOUBLE_CLICK_TIME = 500


def run(q_send, q_receive):

    last_activity = 0
    state = ipc.Type.StartTracking

    mouse_double_click = DOUBLE_CLICK_TIME / 1000 * UPDATES_PER_SECOND

    state_mouse_inactive = False
    state_mouse_clicks: dict[int, tuple[int, int]] = {}
    state_mouse_position: tuple[0, 0] = cursor_position()
    state_monitor_data: list[tuple[int, int, int, int]] = monitor_locations()

    for tick in utils.ticks(UPDATES_PER_SECOND):

        # Process messages from the queue
        while not q_receive.empty():
            received_message = q_receive.get()
            print(f'Tracking received message: {received_message}')

            match received_message.type:
                case ipc.Type.PauseTracking | ipc.Type.StartTracking | ipc.Type.StopTracking:
                    state = received_message.type

        # Handle tracking states
        if state == ipc.Type.PauseTracking:
            continue
        if state == ipc.Type.StopTracking:
            print('Shutting down tracking process...')
            return

        mouse_position = cursor_position()

        # Check if mouse position is inactive (such as a screensaver)
        # If so then wait and try again
        if mouse_position is None:
            if not state_mouse_inactive:
                print('Mouse Undetected')
                state_mouse_inactive = True
            time.sleep(2)
            continue
        if state_mouse_inactive:
            print('Mouse detected')
            state_mouse_inactive = False

        # Update mouse movement
        if mouse_position != state_mouse_position:
            state_mouse_position = mouse_position
            last_activity = tick
            q_send.put(ipc.QueueItem(ipc.Target.Processing, ipc.Type.MouseMove, mouse_position))

        for mouse_button, clicked in get_mouse_click().items():
            if not clicked:
                continue
            click_start, click_latest = state_mouse_clicks.get(mouse_button, (0, 0))
            last_activity = tick

            # First click
            if click_latest != tick - 1:
                # Check if previous click was within the double click period
                if click_start + mouse_double_click > tick:
                    q_send.put(ipc.QueueItem(ipc.Target.Processing, ipc.Type.MouseDoubleClick, mouse_button))
                else:
                    q_send.put(ipc.QueueItem(ipc.Target.Processing, ipc.Type.MouseClick, mouse_button))
                state_mouse_clicks[mouse_button] = (tick, tick)

            # Being held
            else:
                state_mouse_clicks[mouse_button] = (click_start, tick)

        # Check resolution and update if required
        if tick and not tick % 60:
            monitor_data = monitor_locations()
            if state_monitor_data != monitor_data:
                state_monitor_data = monitor_data
                last_activity = tick
