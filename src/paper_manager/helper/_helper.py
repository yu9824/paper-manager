import importlib.util
import inspect
import sys
import warnings
from collections.abc import Callable, Iterable, Iterator
from functools import wraps
from typing import Any, Optional, TypeVar

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

P = ParamSpec("P")
R = TypeVar("R")

T = TypeVar("T")


def is_installed(package_name: str) -> bool:
    """
    Check whether a given Python package is installed.

    Uses `importlib.util.find_spec` to determine if the specified package
    can be imported.

    Parameters
    ----------
    package_name : str
        The name of the package (e.g., "sklearn").

    Returns
    -------
    bool
        True if the package is installed, False otherwise.
    """
    return bool(importlib.util.find_spec(package_name))


def is_argument(__callable: "Callable[..., Any]", arg_name: str) -> bool:
    """
    Check if a given argument name is present in the callable's signature.

    This function checks whether the specified argument name is part of the
    parameters in the callable's signature. It can be used to verify if a
    function or method accepts a specific argument.

    Parameters
    ----------
    __callable : Callable
        The callable (function or method) whose signature is inspected.
    arg_name : str
        The name of the argument to check for in the callable's signature.

    Returns
    -------
    bool
        True if the argument name is found in the callable's parameters, False otherwise.
    """
    return arg_name in set(inspect.signature(__callable).parameters.keys())


class dummy_tqdm(Iterable[T]):
    """
    A dummy class that mimics the behavior of 'tqdm' for testing or placeholder purposes.

    This class allows you to use a tqdm-like interface in cases where the
    progress bar functionality is not needed or when testing code without
    depending on the actual `tqdm` library.

    Parameters
    ----------
    __iterable : Iterable[T]
        An iterable object that will be wrapped and returned by the class.

    Methods
    -------
    __iter__() -> Iterator[T]
        Returns an iterator for the provided iterable.
    __getattr__(name: str) -> Callable[..., None]
        Returns a no-operation function for unsupported attributes.
    """

    def __init__(self, __iterable: "Iterable[T]", *args, **kwargs) -> None:
        self.__iterable = __iterable

    def __iter__(self) -> "Iterator[T]":
        """
        Return an iterator for the given iterable.

        Returns
        -------
        Iterator[T]
            An iterator for the provided iterable object.
        """
        return iter(self.__iterable)

    def __getattr__(self, name: str) -> "Callable[..., None]":
        """
        Handle unsupported attribute access by returning a no-op function.

        This method allows the class to simulate the behavior of tqdm by
        returning a no-op function for any attribute that is not defined.

        Parameters
        ----------
        name : str
            The name of the attribute being accessed.

        Returns
        -------
        Callable[..., None]
            A no-op function that does nothing.
        """
        return self.__no_operation

    @staticmethod
    def __no_operation(*args, **kwargs) -> None:
        """
        A no-operation function used as a placeholder.

        This function does nothing and is used as a fallback for unsupported
        method calls or attributes.

        Returns
        -------
        None
        """
        return


def deprecated(
    reason: Optional[str] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to mark a function as deprecated.

    Parameters
    ----------
    reason : str, optional
        Explanation of why the function is deprecated or what should be used instead.

    Returns
    -------
    Callable
        A decorator that wraps the target function and emits a ``DeprecationWarning``
        upon each call.

    Notes
    -----
    - Preserves the wrapped function's type signature.
    - Emits a ``DeprecationWarning`` every time the function is called.
    - Appends a ``.. deprecated::`` directive to the function's docstring.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        base_message = f"{func.__name__} is deprecated."
        if reason:
            base_message += f" {reason}"

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            warnings.warn(base_message, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        # Add deprecation notice to docstring in NumPy style
        original_doc = func.__doc__ or ""
        deprecation_header = f".. deprecated::\n    {reason or ''}\n\n"
        wrapper.__doc__ = deprecation_header + original_doc

        return wrapper

    return decorator


def split(s: str, sep: str) -> tuple[str, ...]:
    """文字列をセパレータで分割し、タプルとして返す。

    Parameters
    ----------
    s : str
        分割する文字列
    sep : str
        セパレータ

    Returns
    -------
    tuple[str, ...]
        分割された文字列のタプル
    """
    if not s:
        return ()
    return tuple(map(str.strip, s.split(sep)))
