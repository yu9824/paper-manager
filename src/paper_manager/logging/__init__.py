"""This module provides a set of utilities for logging in Python.

It includes functions to configure logging behavior, such as enabling or disabling
the default handler, retrieving a child logger, and accessing the root logger.
Additionally, this module defines constants for various logging levels to be used
in logging configurations.

"""

from logging import CRITICAL, DEBUG, ERROR, INFO, NOTSET, WARNING

from ._logging import (
    catch_all_handler,
    catch_default_handler,
    disable_default_handler,
    enable_default_handler,
    get_child_logger,
    get_handler,
    get_library_root_logger,
)

__all__ = (
    "catch_all_handler",
    "catch_default_handler",
    "disable_default_handler",
    "enable_default_handler",
    "get_child_logger",
    "get_handler",
    "get_library_root_logger",
    "CRITICAL",
    "DEBUG",
    "ERROR",
    "INFO",
    "NOTSET",
    "WARNING",
)
