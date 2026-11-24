"""Validate one JSON or YAML instance against a LinkML target class."""
import argparse
import sys
from pathlib import Path

from common import parse_data
from schema_validation import ValidationEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--target-class", required=True)
    parser.add_argument("--tag")
    parser.add_argument("--revision")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    engine = ValidationEngine()
    try:
        data = parse_data(args.file.read_text(encoding="utf-8"), args.file.suffix)
        errors, notes = engine.validate(args.schema, args.target_class, data, args.tag, args.revision)
        for note in notes:
            print(note)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("Validation passed")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Validation could not be completed: {error}", file=sys.stderr)
        sys.exit(2)
