import importlib.util
import os
import re
import sys
from logging import (
    INFO,
    NOTSET,
    FileHandler,
    Formatter,
    Handler,
    Logger,
    NullHandler,
    StreamHandler,
    getLogger,
)
from types import TracebackType
from typing import Optional, TypeVar

HandlerType = TypeVar("HandlerType", bound=Handler)


def _color_supported() -> bool:
    """
    Check if color output is supported in the current environment.

    This function checks multiple conditions to determine if colored logging
    output should be used:
    1. The `colorlog` package is available
    2. The `NO_COLOR` environment variable is not set
    3. Standard error output is connected to a TTY

    Returns
    -------
    bool
        True if color output is supported, False otherwise.
    """
    if not importlib.util.find_spec("colorlog"):
        return False

    # NO_COLOR environment variable:
    if os.environ.get("NO_COLOR", None):
        return False

    if not hasattr(sys.stderr, "isatty") or not sys.stderr.isatty():
        return False
    else:
        return True


_default_handler: Optional[StreamHandler] = None
"""default root logger handler

if not configured, None
"""


def _is_file_handler(handler: Handler) -> bool:
    """
    Check if a handler is a file-based handler.

    This function determines whether a handler writes to a file rather than
    a stream (like stdout/stderr). File handlers should not use color codes
    as they would be written as literal characters in the file.

    Parameters
    ----------
    handler : Handler
        The handler instance to check.

    Returns
    -------
    bool
        True if the handler is file-based (e.g., FileHandler,
        RotatingFileHandler, TimedRotatingFileHandler), False otherwise.
    """
    return isinstance(handler, FileHandler)


def _get_library_name() -> str:
    """
    Get the root package name for logger configuration.

    Extracts the top-level package name from the current module's `__name__`.
    This is used as the root logger name to ensure proper logger hierarchy
    within the package.

    Returns
    -------
    str
        The root package name (e.g., 'python_template').

    Examples
    --------
    If called from `python_template.logging._logging`, returns 'python_template'.
    """
    return __name__.split(".")[0]


def create_default_formatter(use_color: Optional[bool] = None) -> Formatter:
    """
    Create a default log formatter with optional color support.

    This function automatically selects an appropriate formatter based on the
    runtime environment and whether color should be used. If color output is
    supported (via `colorlog` and TTY) and `use_color` is True or None,
    a `ColoredFormatter` is used. Otherwise, a standard `Formatter` is returned.

    The format string includes:
    - Timestamp (%(asctime)s)
    - Logger name and line number (%(name)s:%(lineno)d)
    - Log level (%(levelname)s) - with color if supported and enabled
    - Message (%(message)s)

    Parameters
    ----------
    use_color : Optional[bool], optional
        Whether to use color in the formatter. If `None` (default), color is
        used only if the environment supports it. If `False`, color is never
        used. If `True`, color is used if available (environment still checked).

    Returns
    -------
    Formatter
        A log formatter instance. Either a `ColoredFormatter` from `colorlog`
        if color is supported and enabled, or a standard `logging.Formatter`
        otherwise.

    See Also
    --------
    _color_supported : Check if color output is supported.
    """
    should_use_color = (use_color is None and _color_supported()) or (
        use_color is True and _color_supported()
    )

    if should_use_color:
        from colorlog import ColoredFormatter

        return ColoredFormatter(
            "%(asctime)s - %(name)s:%(lineno)d%(log_color)s[%(levelname)s]%(reset)s - %(message)s"
        )
    else:
        return Formatter(
            "%(asctime)s - %(name)s:%(lineno)d[%(levelname)s] - %(message)s"
        )


default_formatter: Formatter = create_default_formatter()
"""
Default log formatter instance used for configuring log output.

This formatter is either colorized or plain depending on environment support.
"""


def get_handler(
    handler: HandlerType,
    formatter: Optional[Formatter] = None,
    level: int = NOTSET,
) -> HandlerType:
    """Configure a log handler with a formatter and log level.

    This function provides a convenient way to configure logging handlers
    by setting both the formatter and log level in a single call. If no
    formatter is specified, an appropriate default formatter will be
    automatically selected based on the handler type:

    - For file-based handlers (FileHandler, RotatingFileHandler, etc.),
        a plain formatter without color codes is used.
    - For stream handlers (StreamHandler, etc.), color is used if
        the environment supports it.

    Parameters
    ----------
    handler : HandlerType
        The log handler instance to configure (e.g., `StreamHandler`,
        `FileHandler`). The handler will be modified in place.
    formatter : Optional[Formatter], optional
        The formatter to apply to the handler. If `None`, an appropriate
        default formatter is used based on the handler type (see
        `create_default_formatter`). Default is `None`.
    level : int, optional
        The logging level threshold for the handler. Only messages at or
        above this level will be processed. Use constants from the `logging`
        module (e.g., `logging.DEBUG`, `logging.INFO`, `logging.WARNING`).
        Default is `logging.NOTSET`.

    Returns
    -------
    HandlerType
        The configured handler instance (same object as the input `handler`).

    Examples
    --------
    >>> from logging import StreamHandler, FileHandler, INFO
    >>> # Stream handler - may use color if supported
    >>> stream_handler = get_handler(StreamHandler(), level=INFO)
    >>> logger.addHandler(stream_handler)
    >>>
    >>> # File handler - always uses plain formatter (no color)
    >>> file_handler = get_handler(FileHandler('app.log'), level=INFO)
    >>> logger.addHandler(file_handler)
    """
    handler.setLevel(level)
    if formatter is None:
        # File handlers should never use color (would write ANSI codes to file)
        use_color = None if not _is_file_handler(handler) else False
        formatter = create_default_formatter(use_color=use_color)
    handler.setFormatter(formatter)
    return handler


def _create_default_handler() -> StreamHandler:
    """
    Create and configure the default stream handler for the library.

    This function creates a `StreamHandler` that writes to `sys.stderr` and
    configures it with the default formatter and log level. This handler is
    used as the default handler for the library's root logger.

    Returns
    -------
    StreamHandler
        A configured `StreamHandler` instance with the default formatter
        and log level applied.

    See Also
    --------
    get_handler : Configure a handler with formatter and level.
    _configure_library_root_logger : Set up the root logger with this handler.
    """
    return get_handler(StreamHandler())


def _configure_library_root_logger() -> None:
    """
    Configure the library's root logger with default settings.

    This function performs one-time initialization of the library's root logger.
    It creates and attaches a default stream handler, sets the logging level to
    `INFO`, and disables propagation to prevent duplicate log messages from
    appearing in parent loggers. This function is idempotent and safe to call
    multiple times; configuration is only applied once.

    The configuration includes:
    - A default stream handler writing to `sys.stderr`
    - Logging level set to `INFO`
    - Propagation disabled to avoid duplicate messages

    Returns
    -------
    None

    See Also
    --------
    get_library_root_logger : Get the configured root logger.
    _create_default_handler : Create the default handler.
    """
    global _default_handler

    if _default_handler:
        # This library has already configured the library root logger.
        return

    _default_handler = _create_default_handler()

    # Apply our default configuration to the library root logger.
    library_root_logger = get_library_root_logger()
    library_root_logger.addHandler(_default_handler)
    library_root_logger.setLevel(INFO)
    library_root_logger.propagate = False


def _reset_library_root_logger() -> None:
    """
    Reset the library's root logger to its unconfigured state.

    This function removes the default handler from the root logger, resets
    the logging level to `NOTSET`, and clears the internal handler reference.
    After calling this function, the root logger will no longer have any
    handlers attached and will not produce any output unless handlers are
    manually added.

    This is primarily used internally for testing or cleanup purposes.
    Regular users should use `disable_default_handler()` instead.

    Returns
    -------
    None

    See Also
    --------
    disable_default_handler : Disable the default handler (recommended).
    _configure_library_root_logger : Reconfigure the root logger.
    """
    global _default_handler
    _default_handler = None

    for _handler in get_library_root_logger().handlers.copy():
        get_library_root_logger().removeHandler(_handler)

    _configure_library_root_logger()


def get_library_root_logger() -> Logger:
    """
    Get the root logger for this library package.

    This function returns the root logger for the library, ensuring it is
    properly configured with a default handler and logging level. The logger
    name corresponds to the top-level package name (e.g., 'python_template').

    The logger is automatically configured on first access with:
    - Default stream handler (with color support if available)
    - Logging level set to `INFO`
    - Propagation disabled

    Returns
    -------
    Logger
        The configured root logger instance for the library package.

    Examples
    --------
    >>> logger = get_library_root_logger()
    >>> logger.info("Library initialized")
    python_template - INFO - Library initialized

    See Also
    --------
    get_child_logger : Get a child logger for a specific module.
    """
    _configure_library_root_logger()

    return getLogger(_get_library_name())


def get_child_logger(name: str, propagate: bool = True) -> Logger:
    """
    Get a child logger for a specific module.

    This function creates and returns a child logger under the library's root
    logger hierarchy. The logger name is derived from the module name, allowing
    for organized and hierarchical logging within the package.

    The `name` parameter should typically be set to `__name__` to automatically
    generate the correct logger name based on the module's full path.

    Parameters
    ----------
    name : str
        The module name, typically `__name__`. Must be either:
        - A name within the library's namespace (e.g., 'python_template.module')
        - The string '__main__' for scripts executed directly
    propagate : bool, optional
        Whether log messages should propagate to parent loggers. If `True`,
        messages will also be handled by parent loggers. Default is `True`.

    Returns
    -------
    Logger
        A configured child logger instance with the specified propagation setting.

    Raises
    ------
    ValueError
        If the provided name does not belong to the library's namespace and
        is not '__main__'. This prevents accidentally creating loggers outside
        the intended hierarchy.

    Examples
    --------
    >>> # In a module: python_template/utils.py
    >>> logger = get_child_logger(__name__)
    >>> logger.info("Module loaded")
    python_template.utils - INFO - Module loaded

    >>> # In __main__ script
    >>> logger = get_child_logger(__name__)
    >>> logger.debug("Debug message")
    """
    root_logger = get_library_root_logger()

    _result_logger = re.match(rf"{_get_library_name()}\.(.+)", name)
    if _result_logger:
        child_logger = root_logger.getChild(_result_logger.group(1))
    elif name == "__main__":
        child_logger = root_logger.getChild(name)
    else:
        raise ValueError("You should use '__name__'.")

    child_logger.propagate = propagate
    return child_logger


def enable_default_handler() -> None:
    """
    Enable the default handler for the library's root logger.

    This function re-attaches the default stream handler to the root logger
    if it has been previously removed (e.g., by `disable_default_handler()`).
    If the handler was never configured, it will be created and attached.

    This is useful for restoring logging output after it has been temporarily
    disabled, or for ensuring logging is enabled after a clean state.

    Returns
    -------
    None

    See Also
    --------
    disable_default_handler : Remove the default handler.
    catch_default_handler : Context manager to temporarily disable the handler.
    """
    _configure_library_root_logger()

    assert _default_handler is not None
    get_library_root_logger().addHandler(_default_handler)


def disable_default_handler() -> None:
    """
    Disable the default handler for the library's root logger.

    This function removes the default stream handler from the root logger,
    effectively suppressing all logging output from the library. The handler
    is not deleted and can be re-attached later using `enable_default_handler()`.

    This is useful for:
    - Temporarily suppressing output during testing
    - Allowing users to configure their own handlers without interference
    - Reducing noise in specific execution contexts

    Returns
    -------
    None

    See Also
    --------
    enable_default_handler : Re-attach the default handler.
    catch_default_handler : Context manager to temporarily disable the handler.
    """
    _configure_library_root_logger()

    assert _default_handler is not None
    get_library_root_logger().removeHandler(_default_handler)


class catch_default_handler:
    """
    Context manager to temporarily suppress the default logging handler.

    This context manager allows you to temporarily disable the default handler
    for the library's root logger. When entering the context, the default
    handler is removed, suppressing all logging output. When exiting (even if
    an exception occurs), the handler is automatically restored.

    This is particularly useful for:
    - Suppressing logs during test execution
    - Creating cleaner output in specific code sections
    - Temporarily redirecting all logging to custom handlers

    Examples
    --------
    >>> from python_template.logging import get_child_logger, catch_default_handler
    >>> logger = get_child_logger(__name__)
    >>>
    >>> logger.info("This will be logged")
    >>> with catch_default_handler():
    ...     logger.info("This message will not be logged")
    ...     logger.debug("Neither will this")
    >>> logger.info("This will be logged again")
    """

    null_handler = NullHandler()

    def __enter__(self) -> None:
        """Enter the context and disable the default handler."""
        disable_default_handler()
        if not get_library_root_logger().hasHandlers():
            get_library_root_logger().addHandler(self.null_handler)

    def __exit__(
        self,
        exc_type: "Optional[type[Exception]]",
        exc_value: Optional[Exception],
        traceback: Optional[TracebackType],
    ) -> None:
        """Exit the context and restore the default handler."""
        enable_default_handler()
        if self.null_handler in get_library_root_logger().handlers:
            get_library_root_logger().removeHandler(self.null_handler)


class catch_all_handler:
    """
    Context manager to temporarily suppress all logging handlers.

    This context manager temporarily removes all handlers from the library's
    root logger when entering the context, and restores them when exiting.
    Unlike `catch_default_handler`, this affects all handlers, including any
    custom handlers that have been added.

    This is useful for:
    - Completely suppressing all logging output during specific operations
    - Testing code paths without any logging interference
    - Temporary isolation of logging behavior

    Examples
    --------
    >>> from python_template.logging import get_child_logger, catch_all_handler
    >>> logger = get_child_logger(__name__)
    >>>
    >>> logger.addHandler(custom_handler)  # Custom handler added
    >>> logger.info("This will be logged by all handlers")
    >>> with catch_all_handler():
    ...     logger.info("No handlers active - this won't be logged")
    ...     logger.error("Errors are also suppressed")
    >>> logger.info("All handlers restored - this will be logged again")
    """

    def __enter__(self) -> None:
        """
        Enter the context and remove all handlers from the root logger.

        All existing handlers are saved internally so they can be restored
        when exiting the context.
        """
        self.root_logger = get_library_root_logger()
        self.handlers = self.root_logger.handlers.copy()
        self.null_handler = NullHandler()

        for handler in self.handlers:
            self.root_logger.removeHandler(handler)
        else:
            self.root_logger.addHandler(self.null_handler)

    def __exit__(
        self,
        exc_type: "Optional[type[Exception]]",
        exc_value: Optional[Exception],
        traceback: Optional[TracebackType],
    ) -> None:
        """
        Exit the context and restore all previously removed handlers.

        Parameters
        ----------
        exc_type : Optional[type[Exception]]
            The exception type, if any exception occurred.
        exc_value : Optional[Exception]
            The exception value, if any exception occurred.
        traceback : Optional[TracebackType]
            The traceback, if any exception occurred.

        Returns
        -------
        None
            Always returns None, allowing exceptions to propagate normally.
        """
        for handler in self.handlers:
            self.root_logger.addHandler(handler)
        else:
            self.root_logger.removeHandler(self.null_handler)
