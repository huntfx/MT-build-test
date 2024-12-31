"""Standard format for data to be sent through communication queues."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
import numpy as np


class Target:
    """System components that can send or receive messages."""

    Hub = 0b1
    Tracking = 0b10
    Processing = 0b100
    GUI = 0b1000


class RenderType(Enum):
    """Possible types of renders."""

    Time = auto()
    TimeSincePause = auto()
    Speed = auto()
    SingleClick = auto()
    DoubleClick = auto()
    HeldClick = auto()


@dataclass
class Message:
    """Represents an item to be passed through a communication queue.

    Attributes:
        target: The intended recipient component of the message.
        type: The type of message being sent.
        data: Optional data payload associated with the message.
    """

    target: int = field(default=0)


@dataclass
class Tick(Message):
    """Send the current tick."""

    target: int = field(default=Target.Processing | Target.GUI, init=False)
    tick: int


@dataclass
class MouseMove(Message):
    """Mouse has moved to a new location on the screen."""

    target: int = field(default=Target.Processing | Target.GUI, init=False)
    position: tuple[int, int]


@dataclass
class MouseClick(Message):
    """Mouse has been clicked."""

    target: int = field(default=Target.GUI | Target.Processing, init=False)
    button: int
    position: tuple[int, int]


@dataclass
class MouseHeld(Message):
    """Mouse button is being held."""

    target: int = field(default=Target.GUI | Target.Processing, init=False)
    button: int
    position: tuple[int, int]


@dataclass
class Traceback(Message):
    """Send data when a traceback is raised."""

    target: int = field(default=Target.Hub, init=False)
    exception: Exception
    traceback: str

    def reraise(self):
        """Re-raise the exception.
        Since the full traceback isn't accessible from a different
        thread, replicate Python's behaviour by showing both exceptions.
        """
        print(self.traceback)
        print('During handling of the above exception, another exception occurred:\n')
        raise self.exception


@dataclass
class TrackingState(Message):
    """Set a tracking state."""

    class State(Enum):
        Start = auto()
        Pause = auto()
        Stop = auto()

    target: int = field(default=Target.Hub | Target.Tracking | Target.Processing, init=False)
    state: State


@dataclass
class MonitorsChanged(Message):
    """Send the location of each monitor when the setup changes."""

    target: int = field(default=Target.GUI | Target.Processing, init=False)
    data: list[tuple[int, int, int, int]]


@dataclass
class RenderRequest(Message):
    """Request a render."""

    target: int = field(default=Target.Processing, init=False)
    type: RenderType
    width: Optional[int]
    height: Optional[int]
    colour_map: str
    sampling: int


@dataclass
class Render(Message):
    """A render has been completed."""

    target: int = field(default=Target.GUI, init=False)
    type: RenderType
    array: np.ndarray
    sampling: int
    tick: int


@dataclass
class Exit(Message):
    """Quit the whole application."""

    target: int = field(default=Target.Hub, init=False)


@dataclass
class DebugRaiseError(Message):
    """Raise an error for debugging."""


@dataclass
class ProcessShutDownNotification(Message):
    """Send a notification from a process that it has ended."""
    target: int = field(default=Target.Hub, init=False)
    source: int
