"""
Optional Tree-sitter Universal AST Helper.
Uses `tree_sitter_language_pack` or `tree_sitter_languages` when installed,
falling back cleanly when absent so core factory runs stay dependency-light.
"""

from typing import Any, Dict, List, Optional, Tuple


def _load_parser(language_name: str):
    """Attempt to load a Tree-sitter parser for `language_name`."""
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
        return get_parser(language_name)
    except Exception:
        pass
    try:
        from tree_sitter_languages import get_parser  # type: ignore
        return get_parser(language_name)
    except Exception:
        pass
    return None


def is_treesitter_available(language_name: str = "python") -> bool:
    """Return True if a Tree-sitter parser is available for `language_name`."""
    return _load_parser(language_name) is not None


def extract_symbols_with_treesitter(language_name: str, source_text: str) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """
    Parse `source_text` via Tree-sitter if available and return
    {'classes': [...], 'functions': [...]} or None when unavailable.
    """
    parser = _load_parser(language_name)
    if parser is None:
        return None

    source_bytes = source_text.encode("utf-8", errors="replace")
    tree = parser.parse(source_bytes)
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []

    class_node_types = {
        "class_declaration", "interface_declaration", "class_specifier",
        "struct_specifier", "struct_item", "type_specifier",
    }
    fn_node_types = {
        "function_declaration", "function_definition", "function_item",
        "method_declaration", "method_definition",
    }

    def _node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _find_name(node) -> str:
        name_child = node.child_by_field_name("name")
        if name_child is not None:
            return _node_text(name_child)
        for ch in node.children:
            if ch.type in ("identifier", "type_identifier", "property_identifier", "field_identifier"):
                return _node_text(ch)
        return ""

    def _walk(node, parent_cls: Optional[str] = None):
        if node.type in class_node_types:
            cname = _find_name(node)
            if cname:
                classes.append({"name": cname, "methods": []})
                for ch in node.children:
                    _walk(ch, parent_cls=cname)
                return
        if node.type in fn_node_types:
            fname = _find_name(node)
            if fname:
                if parent_cls and classes:
                    classes[-1]["methods"].append(fname)
                else:
                    functions.append({"name": fname})
                return
        for ch in node.children:
            _walk(ch, parent_cls=parent_cls)

    _walk(tree.root_node)
    return {"classes": classes, "functions": functions}
