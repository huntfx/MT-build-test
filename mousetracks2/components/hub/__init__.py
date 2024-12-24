"""Setup individual components to the Mouse Tracks application.

Components:
    - tracking
    - processing
    - gui
    - cli
"""

import multiprocessing

from .. import ipc, tracking


class Hub:
    """Set up individual components with queues for communication."""

    def __init__(self):
        self._q_main = multiprocessing.Queue()
        self._q_tracking = multiprocessing.Queue()
        self._q_processing = multiprocessing.Queue()

        self._p_tracking = multiprocessing.Process(target=tracking.track, args=(self._q_main, self._q_tracking))

    def start_tracking(self):
        print('Starting up tracking threads...')
        self._p_tracking.start()

    def start_queue_handler(self):
        print('Listening for messages...')
        while True:
            received_message = self._q_main.get()
            match received_message.target:
                case ipc.Target.Hub:
                    print(f'Hub received message: {received_message}')
                case ipc.Target.Tracking:
                    self._q_tracking.put(received_message)
                case _:
                    print(f'Unknown message: {received_message}')


if __name__ == '__main__':
    hub = Hub()
    hub.start_tracking()
    hub.start_queue_handler()
