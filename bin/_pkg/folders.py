"""Manage the index's `folders[]` array (pre-created empty folders)."""

from __future__ import annotations

from . import index as _index


def add_folder(index_path: str, folder: str) -> None:
    def mutator(data: dict) -> dict:
        folders = data.setdefault("folders", [])
        if folder not in folders:
            folders.append(folder)
        return data
    _index.mutate(index_path, mutator)


def remove_folder(index_path: str, folder: str) -> None:
    def mutator(data: dict) -> dict:
        data["folders"] = [f for f in data.get("folders", []) if f != folder]
        return data
    _index.mutate(index_path, mutator)
