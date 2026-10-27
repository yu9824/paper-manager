"""汎用ユーティリティ (``paper_manager.helper``) の単体テスト.

対象は次のとおり。

- 文字列の分割 (``split``): 各要素の前後の空白が除かれたタプルが返り、
  空文字列からは空のタプルが返ること (要件 5.1)。著者やタグの分割に使われ、
  空文字列が空のタプルになる挙動は、著者のないエントリの既知バグ (KB2a) が
  起きる前提でもある
- 非推奨デコレータ (``deprecated``): 呼び出しのたびに ``DeprecationWarning``
  が出て、元の戻り値が返り、``__doc__`` に非推奨の注記が付くこと (要件 5.2)
- インストール有無の判定 (``is_installed``): インストール済みのパッケージでは
  真、存在しないパッケージでは偽になること (要件 5.3)
- 引数の有無の判定 (``is_argument``): シグネチャに含まれる引数名では真、
  含まれない引数名では偽になること (要件 5.4)
- 進捗表示の代替 (``dummy_tqdm``): 反復結果が元の反復可能オブジェクトと同じ
  になり、それ以外の任意の操作が何もせずに完了すること (要件 5.5)
- ダミー関数 (``dummy_func``): 追加の引数に関係なく第 1 引数がそのまま返る
  こと (要件 5.6)

``dummy_tqdm`` は ``tqdm`` を import せず、``tqdm`` の有無で実装を切り替える
こともない。そのため ``tqdm`` が未導入の環境でも同じ結果になる。そのことは
``tqdm`` の import を塞いだ状態でも使えるかどうかで確かめる (``tqdm`` の導入
状況に依存しない)。パッケージの追加は行わない。

非推奨デコレータのテストは、デコレータの適用をテスト関数の中で行う。警告の
検証には ``pytest.warns`` を使い、警告フィルタをグローバルに変更しない。
"""

import inspect
import sys
from collections.abc import Callable, Iterable
from typing import Any, Optional

import pytest

from paper_manager.helper import (
    deprecated,
    dummy_func,
    dummy_tqdm,
    is_argument,
    is_installed,
    split,
)

# --- split (要件 5.1) -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "sep", "expected"),
    [
        pytest.param("a,b,c", ",", ("a", "b", "c"), id="no-space"),
        pytest.param(" a , b ,c ", ",", ("a", "b", "c"), id="around-elements"),
        pytest.param("\ta\n;  b ", ";", ("a", "b"), id="tab-and-newline"),
        pytest.param(
            "Alice Smith and Bob Jones",
            " and ",
            ("Alice Smith", "Bob Jones"),
            id="multi-character-separator",
        ),
        pytest.param(
            "日本語 , 英語",
            ",",
            ("日本語", "英語"),
            id="non-ascii",
        ),
        pytest.param("single", ",", ("single",), id="no-separator"),
        pytest.param("  single  ", ",", ("single",), id="strip-single"),
    ],
)
def test_split_returns_tuple_with_whitespace_stripped_from_each_element(
    text: str, sep: str, expected: tuple[str, ...]
) -> None:
    """各要素の前後の空白が除かれたタプルが返る (要件 5.1)."""
    result = split(text, sep)

    assert isinstance(result, tuple)
    assert result == expected


def test_split_keeps_whitespace_inside_an_element() -> None:
    """要素の内部の空白は除かれない (前後だけを除く)."""
    assert split("  Alice  Smith , Bob  Jones ", ",") == (
        "Alice  Smith",
        "Bob  Jones",
    )


@pytest.mark.parametrize("sep", [",", ";", " and ", "|"])
def test_split_empty_string_returns_empty_tuple(sep: str) -> None:
    """空文字列からは空のタプルが返る (要件 5.1).

    空文字列から要素 1 つの ``("",)`` ではなく空のタプルが返ることが、
    著者やタグの欠落を空の一覧として扱う呼び出し側の前提になっている。
    """
    result = split("", sep)

    assert result == ()
    assert isinstance(result, tuple)


# --- deprecated (要件 5.2) ------------------------------------------------

REASON = "use new_add instead"
ORIGINAL_DOC = "Add ``y`` to ``x``."


def make_deprecated_add(
    reason: Optional[str] = REASON,
) -> Callable[..., int]:
    """``deprecated`` を適用した足し算の関数を作る.

    デコレータの適用はこの関数を呼んだ時点で行う (import 時ではない)。

    Parameters
    ----------
    reason : Optional[str], default REASON
        ``deprecated`` に渡す理由。

    Returns
    -------
    Callable[..., int]
        ``add(x, y=1)``。説明文は ``ORIGINAL_DOC``。
    """

    @deprecated(reason)
    def add(x: int, y: int = 1) -> int:
        """Add ``y`` to ``x``."""
        return x + y

    return add


def test_deprecated_call_emits_deprecation_warning() -> None:
    """非推奨の関数を呼ぶと ``DeprecationWarning`` が出る (要件 5.2)."""
    add = make_deprecated_add()

    with pytest.warns(DeprecationWarning):
        add(1)


def test_deprecated_warns_on_every_call() -> None:
    """同じ場所から繰り返し呼んでも、呼ぶたびに警告が出る (要件 5.2)."""
    add = make_deprecated_add()

    with pytest.warns(DeprecationWarning) as record:
        for _ in range(3):
            add(1)

    emitted = [w for w in record if issubclass(w.category, DeprecationWarning)]
    assert len(emitted) == 3


def test_deprecated_warning_message_names_function_and_reason() -> None:
    """警告のメッセージに関数名と理由が含まれる."""
    add = make_deprecated_add(REASON)

    with pytest.warns(DeprecationWarning) as record:
        add(1)

    message = str(record[0].message)
    assert "add is deprecated" in message
    assert "deprecated" in message
    assert REASON in message


def test_deprecated_without_reason_still_warns_with_function_name() -> None:
    """理由を省略しても警告が出て、関数名が含まれる."""
    add = make_deprecated_add(None)

    with pytest.warns(DeprecationWarning, match=r"add is deprecated"):
        add(1)


def test_deprecated_returns_original_return_value() -> None:
    """元の関数の戻り値がそのまま返る (要件 5.2)."""
    add = make_deprecated_add()

    with pytest.warns(DeprecationWarning):
        results = (add(2), add(2, 5), add(2, y=7))

    assert results == (3, 7, 9)


def test_deprecated_returns_the_same_object_as_original() -> None:
    """戻り値は複製ではなく元の関数が返したオブジェクトそのもの."""
    sentinel = ["not", "copied"]

    @deprecated(REASON)
    def give() -> list[str]:
        return sentinel

    with pytest.warns(DeprecationWarning):
        result = give()

    assert result is sentinel


def test_deprecated_passes_arguments_to_original_function() -> None:
    """位置引数とキーワード引数が元の関数にそのまま渡る."""

    @deprecated(REASON)
    def echo(*args: Any, **kwargs: Any) -> tuple[tuple[Any, ...], dict]:
        return args, kwargs

    with pytest.warns(DeprecationWarning):
        result = echo(1, "two", key="value")

    assert result == ((1, "two"), {"key": "value"})


def test_deprecated_docstring_gets_deprecation_note() -> None:
    """説明文の先頭に非推奨の注記が付き、元の説明文が残る (要件 5.2)."""
    add = make_deprecated_add(REASON)

    doc = add.__doc__
    assert doc is not None
    assert doc.startswith(".. deprecated::")
    assert REASON in doc
    assert ORIGINAL_DOC in doc
    # 注記が先で、元の説明文はその後ろに続く
    assert doc.index(REASON) < doc.index(ORIGINAL_DOC)


def test_deprecated_docstring_is_marked_even_without_reason() -> None:
    """理由がなくても説明文に非推奨の注記が付く."""
    add = make_deprecated_add(None)

    doc = add.__doc__
    assert doc is not None
    assert doc.startswith(".. deprecated::")
    assert ORIGINAL_DOC in doc


def test_deprecated_docstring_is_marked_without_original_docstring() -> None:
    """説明文のない関数でも、注記だけの説明文になる."""

    @deprecated(REASON)
    def undocumented() -> None:
        return None

    doc = undocumented.__doc__
    assert isinstance(doc, str)
    assert doc.startswith(".. deprecated::")
    assert REASON in doc


def test_deprecated_preserves_function_name_and_signature() -> None:
    """関数名とシグネチャは元の関数のまま保たれる."""

    def original(x: int, y: int = 1) -> int:
        return x + y

    wrapped = deprecated(REASON)(original)

    assert wrapped.__name__ == "original"
    assert inspect.signature(wrapped) == inspect.signature(original)


# --- is_installed (要件 5.3) ----------------------------------------------


@pytest.mark.parametrize(
    "package_name",
    ["pytest", "json", "streamlit", "paper_manager"],
)
def test_is_installed_returns_true_for_installed_package(
    package_name: str,
) -> None:
    """インストール済みのパッケージでは真になる (要件 5.3)."""
    assert is_installed(package_name) is True


@pytest.mark.parametrize(
    "package_name",
    ["paper_manager_no_such_package", "zzz_not_installed_for_tests"],
)
def test_is_installed_returns_false_for_missing_package(
    package_name: str,
) -> None:
    """存在しないパッケージでは偽になる (要件 5.3)."""
    assert is_installed(package_name) is False


# --- is_argument (要件 5.4) -----------------------------------------------


def sample_function(
    first: int,
    second: int = 2,
    *args: Any,
    keyword_only: int = 3,
    **kwargs: Any,
) -> int:
    """引数の有無の判定に使う関数 (本体の局所変数は ``body_only``)."""
    body_only = first + second
    return body_only


class SampleClass:
    """メソッドの引数の有無の判定に使うクラス."""

    def method(self, value: int, option: bool = False) -> int:
        """引数 ``value`` と ``option`` を持つメソッド."""
        return value if option else 0


@pytest.mark.parametrize(
    "arg_name",
    [
        pytest.param("first", id="positional"),
        pytest.param("second", id="with-default"),
        pytest.param("keyword_only", id="keyword-only"),
    ],
)
def test_is_argument_returns_true_for_name_in_signature(
    arg_name: str,
) -> None:
    """シグネチャに含まれる引数名では真になる (要件 5.4)."""
    assert is_argument(sample_function, arg_name) is True


@pytest.mark.parametrize(
    "arg_name",
    [
        pytest.param("third", id="absent"),
        pytest.param("firs", id="prefix-of-existing"),
        pytest.param("first_", id="extension-of-existing"),
        pytest.param("FIRST", id="different-case"),
        pytest.param("body_only", id="local-variable-only"),
        pytest.param("", id="empty-name"),
    ],
)
def test_is_argument_returns_false_for_name_not_in_signature(
    arg_name: str,
) -> None:
    """シグネチャに含まれない引数名では偽になる (要件 5.4)."""
    assert is_argument(sample_function, arg_name) is False


def test_is_argument_works_for_methods_and_lambdas() -> None:
    """関数だけでなくメソッドとラムダ式にも使える."""
    method = SampleClass().method

    assert is_argument(method, "value") is True
    assert is_argument(method, "option") is True
    assert is_argument(method, "missing") is False

    def square(number: int) -> int:
        return number * number

    assert is_argument(lambda left, right=0: left + right, "right") is True
    assert is_argument(lambda left, right=0: left + right, "number") is False
    assert is_argument(square, "number") is True


# --- dummy_tqdm (要件 5.5) ------------------------------------------------


@pytest.mark.parametrize(
    "iterable",
    [
        pytest.param([3, 1, 2], id="list"),
        pytest.param((1, "a", None), id="tuple"),
        pytest.param(range(5), id="range"),
        pytest.param("abc", id="str"),
        pytest.param({"k1": 1, "k2": 2}, id="dict"),
        pytest.param([], id="empty"),
    ],
)
def test_dummy_tqdm_iterates_same_items_as_original(
    iterable: Iterable[Any],
) -> None:
    """反復結果は元の反復可能オブジェクトと同じ (順序も同じ) (要件 5.5)."""
    assert list(dummy_tqdm(iterable)) == list(iterable)


def test_dummy_tqdm_yields_the_original_objects() -> None:
    """反復で得られるのは元の要素そのもの (複製ではない)."""
    items = [object(), object(), object()]

    yielded = list(dummy_tqdm(items))

    assert len(yielded) == len(items)
    assert all(a is b for a, b in zip(yielded, items))


def test_dummy_tqdm_wraps_a_generator() -> None:
    """ジェネレータを渡しても、同じ要素が順に得られる."""

    def numbers() -> Iterable[int]:
        yield from (10, 20, 30)

    assert list(dummy_tqdm(numbers())) == [10, 20, 30]


def test_dummy_tqdm_can_be_iterated_repeatedly_over_a_list() -> None:
    """繰り返し反復できる入力は、何度反復しても同じ結果になる."""
    wrapper = dummy_tqdm([1, 2, 3])

    assert list(wrapper) == [1, 2, 3]
    assert list(wrapper) == [1, 2, 3]


def test_dummy_tqdm_for_loop_visits_every_item() -> None:
    """``for`` 文で、元の反復可能オブジェクトと同じ要素を順に処理できる."""
    visited = []

    for item in dummy_tqdm(["x", "y", "z"]):
        visited.append(item)

    assert visited == ["x", "y", "z"]


def test_dummy_tqdm_accepts_extra_arguments_like_tqdm() -> None:
    """``tqdm`` と同じ追加の引数を受け取り、反復結果に影響しない."""
    wrapper = dummy_tqdm(
        [1, 2, 3], "positional", desc="loading", total=3, leave=False
    )

    assert list(wrapper) == [1, 2, 3]


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        pytest.param("update", (1,), {}, id="update"),
        pytest.param("set_description", ("desc",), {}, id="set_description"),
        pytest.param("set_postfix", (), {"loss": 0.5}, id="set_postfix"),
        pytest.param("refresh", (), {}, id="refresh"),
        pytest.param("reset", (), {"total": 3}, id="reset"),
        pytest.param("write", ("message",), {}, id="write"),
        pytest.param("close", (), {}, id="close"),
        pytest.param(
            "never_defined_operation",
            (1, "two"),
            {"key": "value"},
            id="arbitrary-name",
        ),
    ],
)
def test_dummy_tqdm_arbitrary_operations_do_nothing(
    method_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """任意の操作は例外なく完了し、何も返さず、何も出力しない (要件 5.5)."""
    items = [1, 2, 3]
    wrapper = dummy_tqdm(items)

    result = getattr(wrapper, method_name)(*args, **kwargs)

    assert result is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    # 操作の前後で、反復結果も元のリストも変わらない
    assert list(wrapper) == [1, 2, 3]
    assert items == [1, 2, 3]


def test_dummy_tqdm_operations_in_loop_do_not_change_iteration() -> None:
    """ループ中に進捗の更新などを呼んでも、反復結果は変わらない."""
    visited = []
    progress = dummy_tqdm(range(4), desc="work", total=4)

    for item in progress:
        progress.set_description(f"item {item}")
        progress.update(1)
        visited.append(item)
    progress.close()

    assert visited == [0, 1, 2, 3]


def test_dummy_tqdm_does_not_depend_on_tqdm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tqdm`` を import できなくても、反復と操作が同じように動く.

    ``sys.modules`` の ``tqdm`` を ``None`` にすると import は
    ``ImportError`` になる。差し替えは ``monkeypatch`` がテストの後に
    元へ戻す (``tqdm`` が元からあれば元のモジュールに、なければ削除する)。
    """
    monkeypatch.setitem(sys.modules, "tqdm", None)
    with pytest.raises(ImportError):
        import tqdm  # noqa: F401

    wrapper = dummy_tqdm([1, 2, 3], desc="no tqdm")

    assert list(wrapper) == [1, 2, 3]
    assert wrapper.update(1) is None
    assert wrapper.close() is None


# --- dummy_func (要件 5.6) ------------------------------------------------

MUTABLE_LIST = [1, 2]
MUTABLE_DICT = {"k": "v"}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(0, id="int"),
        pytest.param(1.5, id="float"),
        pytest.param("text", id="str"),
        pytest.param(None, id="none"),
        pytest.param(MUTABLE_LIST, id="list"),
        pytest.param(MUTABLE_DICT, id="dict"),
        pytest.param((1, 2), id="tuple"),
        pytest.param(object(), id="object"),
    ],
)
def test_dummy_func_returns_first_argument_as_is(value: Any) -> None:
    """第 1 引数だけを渡すと、そのままのオブジェクトが返る (要件 5.6)."""
    assert dummy_func(value) is value


@pytest.mark.parametrize(
    ("extra_args", "extra_kwargs"),
    [
        pytest.param((1,), {}, id="one-positional"),
        pytest.param(("a", None, [3]), {}, id="many-positional"),
        pytest.param((), {"key": "value"}, id="keyword"),
        pytest.param((1, 2), {"x_": 3, "other": object()}, id="both"),
    ],
)
def test_dummy_func_ignores_additional_arguments(
    extra_args: tuple[Any, ...], extra_kwargs: dict[str, Any]
) -> None:
    """追加の引数に関係なく、第 1 引数がそのまま返る (要件 5.6)."""
    first = ["first"]

    result = dummy_func(first, *extra_args, **extra_kwargs)

    assert result is first
    assert first == ["first"]


def test_dummy_func_does_not_copy_or_modify_the_argument() -> None:
    """返るのは複製ではなく同じオブジェクトで、内容も変わらない."""
    original = {"a": [1, 2, 3]}
    snapshot = {"a": [1, 2, 3]}

    result = dummy_func(original, "ignored")

    assert result is original
    assert original == snapshot


def test_dummy_func_returns_first_not_second_argument() -> None:
    """追加の引数が第 1 引数と異なるオブジェクトでも、返るのは第 1 引数."""
    first, second = object(), object()

    assert dummy_func(first, second) is first
    assert dummy_func(first, second) is not second
