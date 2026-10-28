"""パスの変換と比較キー (``paper_manager.helper._path``) の単体テスト.

対象は次のとおり。

- 拡張長のパスへの変換 (``_to_extended_length``): ``ntpath`` の意味論で
  絶対パスに正規化してから ``\\\\?\\`` を付けること。``/`` 区切りは ``\\``
  になり、``..`` は畳まれ、UNC は ``\\\\?\\UNC\\`` になり、変換済みのパスは
  そのまま返ること (冪等)。ドライブ相対などの完全修飾でないパスは、
  Windows では ``abspath`` で解決してから変換し、解決できない環境では
  prefix を付けずにそのまま返すこと (要件 1.1)
- 表示用の逆変換 (``_from_extended_length``): prefix を外したパスを返し、
  変換の結果を逆変換すると ``ntpath.abspath`` の結果に戻ること (要件 1.4)
- OS による切り替え (``_select_converters``): Windows (``os.name == "nt"``)
  では上の 2 つ、それ以外ではパスを変えない変換を選ぶこと。Windows 以外では
  ``to_fs_path`` が ``Path(p)``、``to_display_path`` が ``str(p)`` に等しく、
  パスを正規化も絶対パス化もしないこと (要件 1.1、1.3)。空のパスは、
  カレントディレクトリを指さないよう ``ValueError`` になること
- 差し替え口 (``_convert`` / ``_unconvert``): ``to_fs_path`` /
  ``to_display_path`` が呼び出し時にモジュールの属性を参照するため、名前で
  import した呼び出し側にも差し替えが効くこと (要件 12.3 の前提)
- Windows のパスの扱いの模倣 (fixture ``windows_like_paths``): 有効にすると
  ``to_fs_path`` の結果が ``//`` で始まる別の形になり、``to_display_path``
  で元の形に戻ること。変換後のパスと変換前のパスの比較が一致せず、
  ``relative_to`` が ``ValueError`` になること (prefix の混在の再現)。
  変換後のパスへのファイル操作が成功すること。fixture を使わないテストでは
  変換が元に戻っていること (要件 12.3)
- フォルダ名の比較キー (``folder_name_key``): 大文字小文字だけ、または
  Unicode 正規化だけが違う名前が同じキーになり、別の名前は別のキーになる
  こと。文字列だけから決まり、OS やファイルシステムに依存しない (要件 3.4)

``_to_extended_length`` と ``_from_extended_length`` は純粋な文字列変換で、
``ntpath`` はどの OS でも動くため、Windows 以外でも検証できる。ドライブ相対
や相対パスの解決だけはカレントディレクトリと OS に依存するため、結果の
期待を ``os.name`` で分ける。``windows_like_paths`` は Windows では
スキップされる (本物の変換が働くため)。
"""

import ntpath
import os
import shutil
import unicodedata
import zipfile
from pathlib import Path
from typing import Union

import pytest

import paper_manager.helper._path as _path
from paper_manager.helper import (
    folder_name_key,
    to_display_path,
    to_fs_path,
)

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"

_non_windows_only = pytest.mark.skipif(
    os.name == "nt", reason="Windows 以外でパスを変えないことの検証"
)

# --- _to_extended_length (要件 1.1) ---------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("C:\\a\\b", _PREFIX + "C:\\a\\b", id="drive"),
        pytest.param("C:/a/b", _PREFIX + "C:\\a\\b", id="forward-slash"),
        pytest.param("C:\\a\\..\\b", _PREFIX + "C:\\b", id="dot-dot"),
        pytest.param(
            "C:\\a\\.\\b\\", _PREFIX + "C:\\a\\b", id="dot-and-trailing-sep"
        ),
        pytest.param(
            "C:\\データ\\論文", _PREFIX + "C:\\データ\\論文", id="non-ascii"
        ),
        pytest.param(
            "\\\\server\\share\\x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc",
        ),
        pytest.param(
            "//server/share/x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc-forward-slash",
        ),
        pytest.param(
            "\\\\server\\share\\a\\..\\x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc-dot-dot",
        ),
    ],
)
def test_to_extended_length_normalizes_then_adds_prefix(
    path: str, expected: str
) -> None:
    """絶対パスに正規化してから prefix を付ける (要件 1.1)."""
    assert _path._to_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("\\\\?\\C:\\a", id="extended"),
        pytest.param("\\\\?\\UNC\\server\\share\\x", id="extended-unc"),
        # prefix 付きのパスでは ``..`` はリテラルの名前なので、畳まない
        pytest.param("\\\\?\\C:\\a\\..\\b", id="extended-with-dot-dot"),
        pytest.param("\\\\.\\C:\\a", id="device"),
    ],
)
def test_to_extended_length_returns_prefixed_path_unchanged(
    path: str,
) -> None:
    """変換済み (device パスを含む) のパスはそのまま返す (冪等)."""
    assert _path._to_extended_length(path) == path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("//?/C:/a", _PREFIX + "C:\\a", id="extended"),
        pytest.param("//./C:/a", "\\\\.\\C:\\a", id="device"),
    ],
)
def test_to_extended_length_keeps_device_path_written_with_slashes(
    path: str, expected: str
) -> None:
    """``/`` で書かれた device パスを UNC と取り違えない."""
    assert _path._to_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "C:\\a\\b",
        "C:/a/../b",
        "\\\\server\\share\\x",
        "//server/share/a/../x",
    ],
)
def test_to_extended_length_is_idempotent(path: str) -> None:
    """変換の結果をもう一度変換しても変わらない."""
    once = _path._to_extended_length(path)

    assert _path._to_extended_length(once) == once


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("C:a\\b", id="drive-relative"),
        pytest.param("\\a\\b", id="rooted-without-drive"),
        pytest.param("a\\b", id="relative"),
    ],
)
def test_to_extended_length_resolves_or_keeps_not_fully_qualified_path(
    path: str,
) -> None:
    """完全修飾でないパスに prefix だけを付けることはしない.

    ``\\\\?\\`` は ``abspath`` の解決を経ないため、``\\\\?\\C:a`` のような
    パスは別の場所を指してしまう。Windows では ``abspath`` で解決してから
    変換し、それ以外の環境 (ドライブのカレントディレクトリがない) では
    prefix を付けずにそのまま返す。
    """
    result = _path._to_extended_length(path)

    if os.name == "nt":
        resolved = ntpath.abspath(path)
        assert result == _PREFIX + resolved
        drive, rest = ntpath.splitdrive(resolved)
        assert drive.endswith(":")
        assert rest.startswith("\\")
    else:
        assert result == path


# --- _from_extended_length (要件 1.4) -------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param(_PREFIX + "C:\\a\\b", "C:\\a\\b", id="extended"),
        pytest.param(
            _UNC_PREFIX + "server\\share\\x",
            "\\\\server\\share\\x",
            id="extended-unc",
        ),
        pytest.param("C:\\a\\b", "C:\\a\\b", id="not-prefixed"),
        pytest.param(
            "\\\\server\\share\\x", "\\\\server\\share\\x", id="plain-unc"
        ),
        pytest.param("/home/user/a", "/home/user/a", id="posix"),
    ],
)
def test_from_extended_length_strips_prefix(path: str, expected: str) -> None:
    """prefix を外したパスを返し、prefix のないパスは変えない (要件 1.4)."""
    assert _path._from_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "C:\\a\\b",
        "C:/a/../b",
        "C:\\データ\\論文.pdf",
        "\\\\server\\share\\x",
        "//server/share/a/../x",
    ],
)
def test_from_extended_length_inverts_to_extended_length(path: str) -> None:
    """変換の結果を逆変換すると ``ntpath.abspath`` の結果に戻る."""
    converted = _path._to_extended_length(path)

    assert _path._from_extended_length(converted) == ntpath.abspath(path)


# --- OS による切り替え (要件 1.1、1.3) ------------------------------------


def test_windows_selects_extended_length_converters() -> None:
    """Windows では拡張長のパスへの変換と、その逆変換を選ぶ."""
    convert, unconvert = _path._select_converters("nt")

    assert convert("C:/a/../b") == _PREFIX + "C:\\b"
    assert unconvert(_PREFIX + "C:\\b") == "C:\\b"


@pytest.mark.parametrize("os_name", ["posix", "java"])
def test_non_windows_selects_converters_that_keep_path(os_name: str) -> None:
    """Windows 以外では、正規化も絶対パス化もしない変換を選ぶ."""
    convert, unconvert = _path._select_converters(os_name)

    for path in ("rel/a/../b", "/abs/a/../b", "C:\\a", _PREFIX + "C:\\a"):
        assert convert(path) == path
        assert unconvert(path) == path


def test_module_converters_match_running_os() -> None:
    """モジュールの変換は、実行中の OS 向けの組み合わせになっている."""
    convert, unconvert = _path._select_converters(os.name)

    assert _path._convert is convert
    assert _path._unconvert is unconvert


@_non_windows_only
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/abs/a/../b", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/abs/データ/論文"), id="path-object"),
    ],
)
def test_to_fs_path_keeps_path_on_non_windows(
    path: Union[str, Path],
) -> None:
    """Windows 以外では ``Path(path)`` をそのまま返す (要件 1.3)."""
    result = to_fs_path(path)

    assert isinstance(result, Path)
    assert result == Path(path)
    assert str(result) == str(Path(path))


@_non_windows_only
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/abs/a/../b", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/abs/データ/論文"), id="path-object"),
    ],
)
def test_to_display_path_keeps_path_on_non_windows(
    path: Union[str, Path],
) -> None:
    """Windows 以外では ``str(path)`` を返し、往復しても変わらない."""
    assert to_display_path(path) == str(path)
    assert to_display_path(to_fs_path(path)) == str(path)


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_to_fs_path_rejects_empty_path(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """空のパスはカレントディレクトリを指してしまうため、例外にする."""
    convert, unconvert = _path._select_converters(os_name)
    monkeypatch.setattr(_path, "_convert", convert)
    monkeypatch.setattr(_path, "_unconvert", unconvert)

    with pytest.raises(ValueError, match="empty"):
        to_fs_path("")


# --- 差し替え口 (要件 12.3 の前提) ----------------------------------------


def test_replacing_convert_affects_name_bound_to_fs_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_convert`` の差し替えが、名前で import した呼び出し側に効く."""
    calls: list[str] = []

    def fake_convert(path: str) -> str:
        calls.append(path)
        return "/alias" + path

    monkeypatch.setattr(_path, "_convert", fake_convert)

    result = to_fs_path(Path("/data/pdf"))

    assert result == Path("/alias/data/pdf")
    assert calls == [os.fspath(Path("/data/pdf"))]


def test_replacing_unconvert_affects_name_bound_to_display_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_unconvert`` の差し替えが、名前で import した呼び出し側に効く."""
    calls: list[str] = []

    def fake_unconvert(path: str) -> str:
        calls.append(path)
        return "shown:" + path

    monkeypatch.setattr(_path, "_unconvert", fake_unconvert)

    result = to_display_path(Path("/data/pdf"))

    assert result == "shown:" + os.fspath(Path("/data/pdf"))
    assert calls == [os.fspath(Path("/data/pdf"))]


def test_windows_converters_through_public_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows の変換に差し替えると、公開関数が拡張長のパスを扱う."""
    convert, unconvert = _path._select_converters("nt")
    monkeypatch.setattr(_path, "_convert", convert)
    monkeypatch.setattr(_path, "_unconvert", unconvert)

    fs_path = to_fs_path("C:/data/a/../pdf")

    assert str(fs_path) == _PREFIX + "C:\\data\\pdf"
    assert to_display_path(fs_path) == "C:\\data\\pdf"


# --- windows_like_paths (要件 12.3) ---------------------------------------
# 別名変換: 絶対パスに正規化してから、前に ``/`` を 1 つ足す (``/x`` を
# ``//x`` にする)。逆変換は先頭の ``//`` を ``/`` に戻す。

_windows_like = pytest.mark.usefixtures("windows_like_paths")


@_windows_like
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/data/pdf", id="absolute"),
        pytest.param("/data/a/../pdf", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/データ/論文"), id="path-object"),
    ],
)
def test_windows_like_paths_changes_form_and_display_restores_it(
    path: Union[str, Path],
) -> None:
    """変換後のパスは ``//`` で始まる別の形になり、表示用の逆変換で戻る."""
    absolute = os.path.abspath(path)

    fs_path = to_fs_path(path)

    assert isinstance(fs_path, Path)
    assert str(fs_path) == "/" + absolute
    assert fs_path.root == "//"
    assert fs_path != Path(absolute)
    assert to_display_path(fs_path) == absolute


@_windows_like
def test_windows_like_paths_is_idempotent() -> None:
    """変換済みのパスはそのまま、変換前のパスの表示は変わらない."""
    once = to_fs_path("/data/pdf")

    assert str(to_fs_path(once)) == str(once)
    assert to_display_path("/data/pdf") == "/data/pdf"


@_windows_like
def test_windows_like_paths_reproduces_prefix_mixing(tmp_path: Path) -> None:
    """変換前と変換後のパスは同じ場所でも、比較・相対パスで一致しない.

    Windows の拡張長のパスと同じく、prefix の混在が不具合になる。変換後の
    空間どうしなら、比較も ``relative_to`` も成り立つ。
    """
    root = tmp_path / "pdf"
    (root / "a").mkdir(parents=True)

    fs_root = to_fs_path(root)
    fs_child = next(fs_root.iterdir())

    assert fs_root.samefile(root)
    assert fs_root != root
    assert fs_root not in {root}
    with pytest.raises(ValueError):
        fs_child.relative_to(root)
    with pytest.raises(ValueError):
        (root / "a").relative_to(fs_root)

    assert fs_child == fs_root / "a"
    assert fs_child.relative_to(fs_root) == Path("a")
    assert to_fs_path(root / "a").relative_to(fs_root) == Path("a")


@_windows_like
def test_windows_like_paths_file_operations_succeed(tmp_path: Path) -> None:
    """変換後のパスへの作成・書き込み・一覧・改名・削除が成功する."""
    data = tmp_path / "data"
    fs_data = to_fs_path(data)
    fs_folder = fs_data / "pdf" / "2020 - Smith - 論文"
    fs_folder.mkdir(parents=True)
    (fs_folder / "00.pdf").write_bytes(b"%PDF-00")
    with open(fs_folder / "01.pdf", "wb") as f:
        f.write(b"%PDF-01")
    (fs_folder / "01.pdf").rename(fs_folder / "02.pdf")

    assert sorted(fs_data.glob("pdf/*/*.pdf")) == [
        fs_folder / "00.pdf",
        fs_folder / "02.pdf",
    ]
    folder = data / "pdf" / "2020 - Smith - 論文"
    assert (folder / "00.pdf").read_bytes() == b"%PDF-00"
    assert (folder / "02.pdf").read_bytes() == b"%PDF-01"

    fs_zip = to_fs_path(tmp_path / "backup.zip")
    with zipfile.ZipFile(fs_zip, "w") as zf:
        for pdf in sorted(fs_data.rglob("*.pdf")):
            zf.write(pdf, pdf.relative_to(fs_data).as_posix())
    with zipfile.ZipFile(tmp_path / "backup.zip") as zf:
        assert zf.namelist() == [
            "pdf/2020 - Smith - 論文/00.pdf",
            "pdf/2020 - Smith - 論文/02.pdf",
        ]

    (fs_folder / "02.pdf").unlink()
    assert not (folder / "02.pdf").exists()
    shutil.rmtree(fs_data)
    assert not data.exists()


@_non_windows_only
def test_conversion_is_restored_without_windows_like_paths() -> None:
    """fixture を使わないテストでは、パスを変えない変換に戻っている.

    上の ``windows_like_paths`` のテストより後に置き、差し替えがテストの
    後に戻ることを既定の順序でも確かめる。
    """
    convert, unconvert = _path._select_converters(os.name)

    assert _path._convert is convert
    assert _path._unconvert is unconvert
    assert str(to_fs_path("/data/pdf")) == "/data/pdf"
    assert to_display_path(Path("/data/pdf")) == "/data/pdf"


# --- folder_name_key (要件 3.4) -------------------------------------------

_NFC_E_ACUTE = "\u00e9"
_NFD_E_ACUTE = "e\u0301"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - smith - deep learning",
            id="case-only",
        ),
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - SMITH - DEEP LEARNING",
            id="upper-case",
        ),
        pytest.param(
            "Caf" + _NFC_E_ACUTE, "Caf" + _NFD_E_ACUTE, id="nfc-and-nfd"
        ),
        pytest.param(
            "CAF\u00c9", "caf" + _NFD_E_ACUTE, id="case-and-normalization"
        ),
        # casefold は NTFS より広く一致させる (同じとみなす安全側)
        pytest.param("Straße", "STRASSE", id="sharp-s"),
        # 結合文字の順序が正規でない名前は、NFD で並べ替えてから casefold
        # しないと、合成済みの文字と同じキーにならない
        pytest.param(
            "\u1fb4", "\u03b1\u0345\u0301", id="combining-mark-order"
        ),
        pytest.param("論文", "論文", id="identical-non-ascii"),
    ],
)
def test_folder_name_key_equal_for_case_or_normalization_only(
    left: str, right: str
) -> None:
    """大文字小文字・Unicode 正規化だけが違う名前は同じキー (要件 3.4)."""
    assert folder_name_key(left) == folder_name_key(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - Smith - Deep Learnings",
            id="extra-char",
        ),
        pytest.param("Caf" + _NFC_E_ACUTE, "Cafe", id="accent"),
        pytest.param("Résumé", "Resume", id="accents"),
        pytest.param("a b", "a  b", id="whitespace"),
        pytest.param("論文", "論分", id="different-kanji"),
    ],
)
def test_folder_name_key_differs_for_different_names(
    left: str, right: str
) -> None:
    """大文字小文字・正規化以外が違う名前は別のキー (要件 3.4)."""
    assert folder_name_key(left) != folder_name_key(right)


def test_folder_name_key_is_nfd_casefolded_string() -> None:
    """キーは NFD の文字列で、もう一度キーにしても変わらない."""
    key = folder_name_key("CAF\u00c9 Straße")

    assert isinstance(key, str)
    assert key == unicodedata.normalize("NFD", key)
    assert key == "caf" + _NFD_E_ACUTE + " strasse"
    assert folder_name_key(key) == key
