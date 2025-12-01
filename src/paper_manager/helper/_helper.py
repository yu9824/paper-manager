import inspect
import pkgutil
import sys

# deprecated in python >=3.12
from typing import TypeVar

if sys.version_info >= (3, 9):
    from collections.abc import Callable
else:
    from typing import Callable

T = TypeVar("T")


PACKAGE_NAMES = {_module.name for _module in pkgutil.iter_modules()}


def is_installed(package_name: str) -> bool:
    """Check if the package is installed.

    Parameters
    ----------
    package_name : str
        package name like `sklearn`

    Returns
    -------
    bool
        if installed, True
    """
    return package_name in PACKAGE_NAMES


def dummy_func(x: T, *args, **kwargs) -> T:
    """dummy function

    Parameters
    ----------
    x : T
        Anything

    Returns
    -------
    T
        same as input
    """
    return x


def is_argument(_callable: Callable, arg_name: str) -> bool:
    return arg_name in inspect.signature(_callable).parameters.keys()


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
