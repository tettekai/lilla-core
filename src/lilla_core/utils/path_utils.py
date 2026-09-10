"""ユーザー入力由来の相対パスを安全に扱うための共通ユーティリティ。

ファイルシステム上のリソースを LLM の入力由来のパスで読み書きするツール
（拡張側の実装）では、``..`` や絶対パスをそのまま受け入れるとルート外・
リポジトリ外を指せてしまう。外部 API 呼び出し用の URL 組み立てとローカル
ファイルの読み取りの双方で同じ検証を共有できるよう、ここに集約する。
"""
from __future__ import annotations

from pathlib import Path


def validate_relative_path(path: str) -> None:
    """root ディレクトリ配下を指す相対パスとして妥当かを検証する。

    文字列としての検証のみを行う（ファイルシステムには触れない）。URL への埋め込みなど、
    実ファイルを解決しない用途でも使えるようにするため分離している。

    Args:
        path: root ディレクトリからの相対パス。

    Raises:
        ValueError: path が空、絶対パス、または ``..`` セグメントを含む場合。
    """
    if not path:
        raise ValueError("Path is empty")
    # Windows 形式の区切りでも同じ判定になるよう / に寄せてから検査する
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"Absolute paths are not allowed: {path}")
    if any(segment == ".." for segment in normalized.split("/")):
        raise ValueError(f"Paths containing parent directory segments are not allowed: {path}")


def resolve_within_root(root: Path, path: str) -> Path:
    """相対パスを検証したうえで root 配下の絶対パスに解決する。

    ``validate_relative_path`` による文字列検証に加え、シンボリックリンク等を
    たどった解決後のパスが root 配下にあることまで確認する。

    Args:
        root: 基準ディレクトリ。
        path: root からの相対パス。

    Returns:
        解決済みの絶対パス。

    Raises:
        ValueError: path が不正、または解決後のパスが root の外を指す場合。
    """
    validate_relative_path(path)
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"Paths pointing outside the root directory are not allowed: {path}")
    return resolved
