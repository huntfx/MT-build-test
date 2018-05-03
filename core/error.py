"""
This is part of the Mouse Tracks Python application.
Source: https://github.com/Peter92/MouseTracks
"""

from __future__ import absolute_import

import sys

from core.base import format_file_path
from core.compatibility import input, Message, PYTHON_VERSION
from core.constants import DEFAULT_PATH
from core.language import STRINGS
from core.os import OPERATING_SYSTEM
from core.versions import VERSION, FILE_VERSION


def handle_error(trace=None, log=True, console=True):
    """Any errors are sent to here."""
    if trace is not None:
    
        #Generate output
        output = ['Mouse Tracks {} ({}) | Python {} | {}'.format(VERSION, FILE_VERSION, PYTHON_VERSION, OPERATING_SYSTEM)]
        output.append('')
        output.append(trace)
        output = '\n'.join(output)
        
        #Write to file
        if log:
            file_name = format_file_path('{}\\error.txt'.format(DEFAULT_PATH))
            with open(file_name, 'w') as f:
                f.write(output)
        Message(trace.strip())
        
        #Output information to quit/restart
        try:
            error_message = STRINGS['Misc']['ProgramError']
        except KeyError:
            error_message = 'An error occurred.'
        try:
            restart_message = STRINGS['Misc']['ProgramRestart']
        except KeyError:
            restart_message = 'Please restart the program...'
        try:
            exit_message = STRINGS['Misc']['ProgramExit']
        except KeyError:
            exit_message = 'Press enter to exit..'

        if console:
            input('{} {}'.format(error_message, exit_message))
        else:
            return Message('{} {}'.format(error_message, restart_message))
    sys.exit(0)
            