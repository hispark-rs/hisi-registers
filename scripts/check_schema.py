"""Validate generated SVDs with the vendored ARM CMSIS-SVD 1.3.9 schema."""
from pathlib import Path
import sys
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]


def validate(paths: list[Path]) -> None:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(ROOT / "schema/CMSIS-SVD-1.3.9.xsd"), parser))
    for path in paths:
        schema.assertValid(etree.parse(str(path), parser))
        print(f"{path.name}: CMSIS-SVD 1.3.9 schema PASS")


if __name__ == "__main__":
    validate([Path(arg) for arg in sys.argv[1:]] or [
        ROOT / "generated" / f"{name}.experimental.svd"
        for name in ("WS53", "WS63", "BS2X")
    ])
