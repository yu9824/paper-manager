import re
from logging import (
    INFO,
    NOTSET,
    Formatter,
    Handler,
    Logger,
    StreamHandler,
    getLogger,
)
from types import TracebackType
from typing import Optional


def _get_root_logger_name() -> str:
    return __name__.split(".")[0]


def _get_default_formatter() -> Formatter:
    return Formatter(
        "%(asctime)s - %(name)s:%(lineno)d[%(levelname)s] - %(message)s",
    )


def get_handler(
    handler: Handler, formatter: Optional[Formatter] = None, level=NOTSET
) -> Handler:
    """configure handler in an easy api

    Parameters
    ----------
    handler : Handler
        _description_
    formatter : Optional[Formatter], optional
        _description_, by default None
    level : _type_, optional
        _description_, by default NOTSET

    Returns
    -------
    Handler
        _description_
    """
    handler.setLevel(level)
    handler.setFormatter(formatter if formatter else _get_default_formatter())
    return handler


def _get_default_handler() -> StreamHandler:
    return get_handler(StreamHandler())


default_handler: StreamHandler = _get_default_handler()
"""default root logger handler"""


def get_root_logger() -> Logger:
    """get root logger of this package

    Returns
    -------
    Logger
        _description_
    """
    root_logger = getLogger(_get_root_logger_name())

    root_logger.addHandler(default_handler)
    root_logger.setLevel(INFO)
    root_logger.propagate = False
    return root_logger


def get_child_logger(name: str, propagate: bool = True) -> Logger:
    """get logger

    Parameters
    ----------
    name : str
        You shold assign '__name__'

    propagate : bool
        propagate to parent handler or not, by default True

    Returns
    -------
    Logger
        child logger

    Raises
    ------
    ValueError

    """
    root_logger = get_root_logger()

    _result_logger = re.match(rf"{_get_root_logger_name()}\.(.+)", name)
    if _result_logger:
        child_logger = root_logger.getChild(_result_logger.group(1))
    elif name == "__main__":
        child_logger = root_logger.getChild(name)
    else:
        raise ValueError("You should use '__name__'.")

    child_logger.propagate = propagate
    return child_logger


def enable_default_handler() -> None:
    get_root_logger().addHandler(default_handler)


def disable_default_handler() -> None:
    get_root_logger().removeHandler(default_handler)


class catch_default_handler:
    """catch default handler

    Example
    -------
    >>> _logger = get_child_logger(__name__)
    >>> with catch_default_handler():
    >>>    _logger.info("not log")
    >>> _logger.info("log")

    """

    def __enter__(self) -> None:
        disable_default_handler()

    def __exit__(
        self,
        exc_type: Optional[type[Exception]],
        exc_value: Optional[Exception],
        traceback: Optional[TracebackType],
    ) -> None:
        enable_default_handler()
