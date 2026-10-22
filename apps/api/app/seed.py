"""Create the schema and load a deterministic sample e-commerce dataset (2024-2025).

Usage: python -m app.seed            # (re)create all tables and load the data
       python -m app.seed --if-empty # only when the tables don't exist yet (safe on every deploy)
The same seed always produces the same rows, so evaluation results are reproducible.
"""

import math
import random
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import get_settings

SEED = 42
START = date(2024, 1, 1)
END = date(2025, 12, 31)
N_CUSTOMERS = 2000

REGIONS = [  # name, country, customer weight, yearly growth
    ("North", "India", 0.24, 0.30),
    ("South", "India", 0.26, 0.45),
    ("West", "India", 0.22, 0.25),
    ("East", "India", 0.14, 0.15),
    ("Middle East", "UAE", 0.08, 0.60),
    ("Southeast Asia", "Singapore", 0.06, 0.35),
]

CATEGORIES = {  # name: (min price, max price, cost ratio, popularity, return rate)
    "Electronics": (1500, 60000, 0.78, 0.20, 0.07),
    "Home & Kitchen": (300, 15000, 0.62, 0.16, 0.04),
    "Fashion": (400, 6000, 0.45, 0.18, 0.11),
    "Books": (150, 1200, 0.70, 0.10, 0.02),
    "Sports & Outdoors": (500, 20000, 0.60, 0.10, 0.05),
    "Beauty": (200, 3500, 0.40, 0.11, 0.03),
    "Toys": (250, 5000, 0.55, 0.07, 0.04),
    "Groceries": (80, 1500, 0.82, 0.08, 0.01),
}

PRODUCT_WORDS = {
    "Electronics": (["Pro", "Lite", "Max", "Mini", "Ultra"], ["Earbuds", "Smartwatch", "Laptop", "Tablet", "Speaker", "Monitor", "Power Bank", "Camera"]),
    "Home & Kitchen": (["Classic", "Smart", "Steel", "Compact", "Deluxe"], ["Mixer", "Air Fryer", "Kettle", "Cookware Set", "Lamp", "Bedsheet", "Water Purifier", "Vacuum"]),
    "Fashion": (["Slim", "Casual", "Linen", "Denim", "Festive"], ["Shirt", "Kurta", "Sneakers", "Jacket", "Saree", "Jeans", "Dress", "Backpack"]),
    "Books": (["Complete", "Illustrated", "Pocket", "Collector's", "Student"], ["Novel", "Cookbook", "Atlas", "Biography", "Workbook", "Comic", "Guide", "Anthology"]),
    "Sports & Outdoors": (["Trail", "Grip", "Aero", "Flex", "Peak"], ["Yoga Mat", "Cricket Bat", "Cycle", "Dumbbells", "Tent", "Football", "Racket", "Bottle"]),
    "Beauty": (["Herbal", "Matte", "Hydra", "Glow", "Pure"], ["Serum", "Lipstick", "Sunscreen", "Face Wash", "Shampoo", "Perfume", "Moisturiser", "Kajal"]),
    "Toys": (["Junior", "Mega", "Wooden", "Magic", "Turbo"], ["Puzzle", "Robot Kit", "Doll House", "Race Car", "Board Game", "Blocks", "Drone", "Plush"]),
    "Groceries": (["Organic", "Daily", "Farm", "Premium", "Value"], ["Basmati Rice", "Green Tea", "Almonds", "Olive Oil", "Honey", "Coffee", "Oats", "Spice Box"]),
}

FIRST = ["Aarav", "Vivaan", "Aditya", "Ananya", "Diya", "Ishaan", "Kavya", "Rohan", "Saanvi", "Arjun", "Meera", "Kabir",
         "Priya", "Rahul", "Neha", "Omar", "Fatima", "Wei", "Mei", "Liam", "Sara", "Vikram", "Pooja", "Nikhil", "Zara"]
LAST = ["Sharma", "Verma", "Iyer", "Reddy", "Nair", "Gupta", "Khan", "Das", "Patel", "Mehta", "Singh", "Rao", "Kapoor",
        "Ali", "Tan", "Lim", "Joshi", "Bose", "Menon", "Chopra"]

SEGMENTS = [("consumer", 0.70), ("small_business", 0.22), ("enterprise", 0.08)]


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _season(d: date) -> float:
    # Festive peak in Oct-Nov, December holidays, quiet February.
    return {1: 0.9, 2: 0.8, 3: 0.95, 4: 0.95, 5: 1.0, 6: 0.95, 7: 1.0, 8: 1.05, 9: 1.1, 10: 1.35, 11: 1.45, 12: 1.25}[d.month]


def generate(rng: random.Random):
    regions = [(i + 1, name, country) for i, (name, country, _, _) in enumerate(REGIONS)]
    categories = [(i + 1, name) for i, name in enumerate(CATEGORIES)]

    products = []  # (id, name, category_id, list_price, unit_cost, launched_on)
    for cat_id, cat in categories:
        lo, hi, cost_ratio, _, _ = CATEGORIES[cat]
        adjs, nouns = PRODUCT_WORDS[cat]
        names = rng.sample([f"{a} {n}" for a in adjs for n in nouns], 15)
        for name in names:
            price = round(math.exp(rng.uniform(math.log(lo), math.log(hi))), -1) - 1
            cost = round(price * cost_ratio * rng.uniform(0.9, 1.1), 2)
            launched = START + timedelta(days=rng.randint(0, 600)) if rng.random() < 0.25 else date(2023, rng.randint(1, 12), 1)
            products.append((len(products) + 1, name, cat_id, price, cost, launched))

    customers = []  # (id, name, email, region_id, segment, signup_date)
    signup_span = (END - date(2023, 1, 1)).days
    for cid in range(1, N_CUSTOMERS + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        region_id = rng.choices(range(1, len(REGIONS) + 1), weights=[r[2] for r in REGIONS])[0]
        segment = rng.choices([s for s, _ in SEGMENTS], weights=[w for _, w in SEGMENTS])[0]
        # Signups accelerate over time (sqrt skews towards later dates).
        signup = date(2023, 1, 1) + timedelta(days=int(signup_span * rng.random() ** 0.8))
        customers.append((cid, f"{first} {last}", f"{first.lower()}.{last.lower()}{cid}@example.com", region_id, segment, signup))
    customers.sort(key=lambda c: c[5])

    # Heavy-tailed loyalty so some customers order far more often than others.
    loyalty = {c[0]: rng.paretovariate(1.6) for c in customers}
    region_growth = {i + 1: r[3] for i, r in enumerate(REGIONS)}
    cat_pop = [CATEGORIES[c][3] for _, c in categories]
    cat_return = {cid: CATEGORIES[c][4] for cid, c in categories}
    by_cat = {cid: [p for p in products if p[2] == cid] for cid, _ in categories}

    orders, items = [], []
    signed_up = 0
    for d in _days(START, END):
        while signed_up < len(customers) and customers[signed_up][5] <= d:
            signed_up += 1
        if signed_up == 0:
            continue
        years = (d - START).days / 365
        expected = 16 * (1.35 ** years) * _season(d) * (1.15 if d.weekday() >= 5 else 1.0)
        n = max(0, int(rng.gauss(expected, math.sqrt(expected))))
        pool = customers[:signed_up]
        weights = [loyalty[c[0]] * (1 + region_growth[c[3]]) ** years for c in pool]
        for cust in rng.choices(pool, weights=weights, k=n):
            oid = len(orders) + 1
            mobile_share = 0.30 + 0.20 * years
            channel = rng.choices(["web", "mobile", "marketplace"], weights=[0.55 - mobile_share * 0.6, mobile_share, 0.45 - mobile_share * 0.4])[0]
            lines = []
            for _ in range(rng.choices([1, 2, 3, 4], weights=[0.5, 0.3, 0.14, 0.06])[0]):
                cat_id = rng.choices(range(1, len(categories) + 1), weights=cat_pop)[0]
                options = [p for p in by_cat[cat_id] if p[5] <= d]
                if not options:
                    continue
                prod = rng.choice(options)
                qty = rng.choices([1, 2, 3, 5, 10], weights=[0.7, 0.18, 0.07, 0.03, 0.02] if cust[4] != "enterprise" else [0.3, 0.25, 0.2, 0.15, 0.1])[0]
                discount = rng.choice([0, 0, 0, 0.05, 0.10]) + (0.10 if d.month in (10, 11) and rng.random() < 0.5 else 0)
                lines.append((prod[0], qty, round(prod[3] * (1 - discount), 2), cat_id))
            if not lines:
                continue
            ret_rate = max(cat_return[line[3]] for line in lines)
            r = rng.random()
            status = "cancelled" if r < 0.06 else "returned" if r < 0.06 + ret_rate else "completed"
            orders.append((oid, cust[0], d, status, channel))
            for prod_id, qty, price, _ in lines:
                items.append((len(items) + 1, oid, prod_id, qty, price))

    return regions, categories, products, customers, orders, items


def seed(database_url: str) -> dict[str, int]:
    rng = random.Random(SEED)
    regions, categories, products, customers, orders, items = generate(rng)
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    schema_sql = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    tables = [
        ("regions", ["region_id", "name", "country"], regions),
        ("customers", ["customer_id", "name", "email", "region_id", "segment", "signup_date"], sorted(customers)),
        ("categories", ["category_id", "name"], categories),
        ("products", ["product_id", "name", "category_id", "list_price", "unit_cost", "launched_on"], products),
        ("orders", ["order_id", "customer_id", "order_date", "status", "channel"], orders),
        ("order_items", ["order_item_id", "order_id", "product_id", "quantity", "unit_price"], items),
    ]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(schema_sql)
        for table, cols, rows in tables:
            with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            # Keep SERIAL sequences in step with the explicit ids.
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{cols[0]}'), (SELECT MAX({cols[0]}) FROM {table}))")
        cur.execute("ANALYZE")
    return {t: len(rows) for t, _, rows in tables}


def is_seeded(database_url: str) -> bool:
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT to_regclass('public.order_items') IS NOT NULL").fetchone()[0]


def ensure_database(database_url: str) -> bool:
    """Create the database named in the URL if it doesn't exist yet. Returns True if it was created."""
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    # Hosted databases (Render, Railway) already exist and may not let us into the "postgres" database.
    try:
        psycopg.connect(dsn).close()
        return False
    except psycopg.OperationalError:
        pass
    params = conninfo_to_dict(dsn)
    name = params.get("dbname") or params.get("user")
    with psycopg.connect(make_conninfo(**{**params, "dbname": "postgres"}), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        return True


if __name__ == "__main__":
    import sys

    settings = get_settings()
    if not settings.sample_data:
        print("SAMPLE_DATA is off; not loading the sample data.")
        raise SystemExit(0)
    url = settings.database_url
    if ensure_database(url):
        print("Created the database.")
    if "--if-empty" in sys.argv and is_seeded(url):
        print("Database already seeded; skipping.")
        raise SystemExit(0)
    counts = seed(url)
    for table, n in counts.items():
        print(f"{table:12} {n:>7} rows")
