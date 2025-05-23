"""Define the application version.
This is used for both the application and build process.

IMPORTANT: Modifying the `__version__` string below and committing this
file to the main branch will automatically trigger a new software release.
This process includes:
1. Creating a Git tag named `v<__version__>` (eg. v1.0.1).
2. Publishing a new GitHub Release with this tag.
3. The release notes will be based on the commit message of this change.
4. An executable will be built and attached to the release.

Some processing will be done on the commit message so the format can be
kept simple. The recommend commit message format is as follows:
    Created new option.
    # Enhancements
    - New option
    # Fixes
    - Fixed potential crash when thing happens (<commit_sha>)
"""

__version__ = '2.0.26'
