"""
Generate synthetic datasets for testing the SQLite source of truth.

The files are written in exactly the shapes import_dataset.py
already understands, so the workflow is:

    python generate_dataset.py --kind all --count 1000
    python import_dataset.py --file datasets/users.csv   --prefix user:
    python import_dataset.py --file datasets/products.json --key-column sku --prefix product:
    python import_dataset.py --file datasets/orders.jsonl --key-column order_id --prefix order:

Kinds:

    users     -> datasets/users.csv      (key column: id)
    products  -> datasets/products.json  (key column: sku)
    orders    -> datasets/orders.jsonl   (key column: order_id)

The output is deterministic for a given --seed, so the same command
always produces byte-identical files.
"""

import argparse
import csv
import json
import os
import random


FIRST_NAMES = [
    "Gaurav", "Alex", "Priya", "Marco", "Sofia", "Kenji", "Amara",
    "Luca", "Nadia", "Omar", "Elena", "Ravi", "Ingrid", "Tomas",
    "Yuki", "Fatima", "Diego", "Anya", "Noah", "Leila",
]

LAST_NAMES = [
    "Sharma", "Mercer", "Patel", "Rossi", "Costa", "Tanaka", "Diallo",
    "Bianchi", "Haddad", "Khan", "Novak", "Iyer", "Larsen", "Silva",
    "Mori", "Aziz", "Reyes", "Petrov", "Walker", "Farah",
]

CITIES = [
    "Berlin", "Mumbai", "Tokyo", "Lisbon", "Nairobi", "Toronto",
    "Lyon", "Osaka", "Bogota", "Warsaw", "Cairo", "Seoul",
]

CATEGORIES = [
    "electronics", "books", "clothing", "toys", "garden",
    "sports", "grocery", "furniture", "beauty", "tools",
]

ORDER_ITEMS = [
    "keyboard", "monitor", "desk lamp", "backpack", "water bottle",
    "notebook", "headphones", "mouse pad", "charger", "mug",
]

ORDER_STATUSES = ["pending", "shipped", "delivered", "cancelled", "refunded"]


# ---------------------------------------------------------
# RECORD BUILDERS
# ---------------------------------------------------------

def make_user(rng: random.Random, index: int) -> dict:
    """One row of datasets/users.csv."""

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)

    return {
        "id": index,
        "name": f"{first} {last}",
        "email": f"{first.lower()}.{last.lower()}{index}@example.com",
        "age": rng.randint(18, 75),
        "city": rng.choice(CITIES),
        "signup_date": f"20{rng.randint(20, 25)}-{rng.randint(1, 12):02d}-"
                       f"{rng.randint(1, 28):02d}",
        "active": rng.random() < 0.8,
        "score": round(rng.uniform(0, 100), 2),
    }


def make_product(rng: random.Random, index: int) -> dict:
    """One record of datasets/products.json."""

    category = rng.choice(CATEGORIES)

    return {
        "sku": f"p-{index:06d}",
        "name": f"{category.title()} item {index}",
        "price": round(rng.uniform(1.5, 999.95), 2),
        "category": category,
        "stock": rng.randint(0, 500),
        "rating": round(rng.uniform(1, 5), 1),
    }


def make_order(rng: random.Random, index: int) -> dict:
    """One line of datasets/orders.jsonl."""

    quantity = rng.randint(1, 5)
    total = round(rng.uniform(5, 400) * quantity, 2)

    return {
        "order_id": f"o-{index:06d}",
        "user_id": rng.randint(1, 10_000),
        "item": rng.choice(ORDER_ITEMS),
        "quantity": quantity,
        "total": total,
        "status": rng.choice(ORDER_STATUSES),
        "created_at": f"2025-{rng.randint(1, 12):02d}-"
                      f"{rng.randint(1, 28):02d}T"
                      f"{rng.randint(0, 23):02d}:"
                      f"{rng.randint(0, 59):02d}:00Z",
    }


# ---------------------------------------------------------
# KIND REGISTRY
# ---------------------------------------------------------

KINDS = {
    "users": {
        "filename": "users.csv",
        "file_format": "csv",
        "key_column": "id",
        "prefix": "user:",
        "builder": make_user,
    },
    "products": {
        "filename": "products.json",
        "file_format": "json",
        "key_column": "sku",
        "prefix": "product:",
        "builder": make_product,
    },
    "orders": {
        "filename": "orders.jsonl",
        "file_format": "jsonl",
        "key_column": "order_id",
        "prefix": "order:",
        "builder": make_order,
    },
}


# ---------------------------------------------------------
# WRITERS
# ---------------------------------------------------------

def write_csv(path: str, records) -> None:

    records = list(records)

    with open(path, "w", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))

        writer.writeheader()
        writer.writerows(records)


def write_json(path: str, records) -> None:

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(list(records), handle, indent=2)


def write_jsonl(path: str, records) -> None:

    with open(path, "w", encoding="utf-8") as handle:

        for record in records:
            handle.write(json.dumps(record) + "\n")


WRITERS = {
    "csv": write_csv,
    "json": write_json,
    "jsonl": write_jsonl,
}


# ---------------------------------------------------------
# GENERATION
# ---------------------------------------------------------

def generate(kind: str, count: int, out_dir: str, seed: int = 42) -> str:
    """
    Build one dataset file and return its path.

    Each kind gets its own RNG stream derived from the seed, so
    adding or removing a kind never changes the others' output.
    """

    if kind not in KINDS:
        supported = ", ".join(sorted(KINDS))
        raise ValueError(f"Unknown kind '{kind}'. Supported: {supported}")

    if count < 1:
        raise ValueError("count must be >= 1")

    spec = KINDS[kind]

    rng = random.Random(f"{seed}:{kind}")

    records = [spec["builder"](rng, index) for index in range(1, count + 1)]

    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, spec["filename"])

    WRITERS[spec["file_format"]](path, records)

    return path


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def parse_args():

    parser = argparse.ArgumentParser(
        description="Generate synthetic CSV/JSON/JSONL datasets "
                    "for import_dataset.py"
    )

    parser.add_argument(
        "--kind",
        default="all",
        choices=["all", *sorted(KINDS)],
        help="Which dataset to generate (default: all)"
    )

    parser.add_argument(
        "--count",
        type=int,
        default=1000,
        help="Records per dataset (default: 1000)"
    )

    parser.add_argument(
        "--out",
        default="datasets",
        help="Output directory (default: datasets)"
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed; same seed = identical files (default: 42)"
    )

    return parser.parse_args()


def main():

    args = parse_args()

    kinds = list(KINDS) if args.kind == "all" else [args.kind]

    print(f"Count per dataset : {args.count}")
    print(f"Output directory   : {args.out}")
    print(f"Seed               : {args.seed}")
    print()

    for kind in kinds:

        path = generate(kind, args.count, args.out, args.seed)

        spec = KINDS[kind]

        print(f"{kind:<10} -> {path}")
        print(f"{'':10}    format={spec['file_format']} "
              f"key={spec['key_column']} prefix={spec['prefix']}")

    print()
    print("Import with:")

    for kind in kinds:

        spec = KINDS[kind]

        print(f"  python import_dataset.py --file "
              f"{args.out}/{spec['filename']} "
              f"--key-column {spec['key_column']} "
              f"--prefix {spec['prefix']}")


if __name__ == "__main__":
    main()
