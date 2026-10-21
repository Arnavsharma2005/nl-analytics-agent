-- Sample e-commerce dataset for the analytics agent.
-- Column comments are read by the agent and included in its prompt, so keep them accurate.

DROP TABLE IF EXISTS order_items, orders, products, categories, customers, regions CASCADE;

CREATE TABLE regions (
    region_id   SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    country     TEXT NOT NULL
);
COMMENT ON TABLE regions IS 'Sales regions. Every customer belongs to one region.';

CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    region_id   INT  NOT NULL REFERENCES regions(region_id),
    segment     TEXT NOT NULL CHECK (segment IN ('consumer', 'small_business', 'enterprise')),
    signup_date DATE NOT NULL
);
COMMENT ON TABLE customers IS 'Customers who have signed up. A customer may have zero or more orders.';
COMMENT ON COLUMN customers.signup_date IS 'Date the customer created their account.';

CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);
COMMENT ON TABLE categories IS 'Product categories.';

CREATE TABLE products (
    product_id  SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id INT  NOT NULL REFERENCES categories(category_id),
    list_price  NUMERIC(10, 2) NOT NULL,
    unit_cost   NUMERIC(10, 2) NOT NULL,
    launched_on DATE NOT NULL
);
COMMENT ON TABLE products IS 'Product catalogue.';
COMMENT ON COLUMN products.list_price IS 'Current catalogue price. The price actually paid is order_items.unit_price.';
COMMENT ON COLUMN products.unit_cost IS 'Cost to the business of one unit; used for profit and margin.';

CREATE TABLE orders (
    order_id    SERIAL PRIMARY KEY,
    customer_id INT  NOT NULL REFERENCES customers(customer_id),
    order_date  DATE NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('completed', 'cancelled', 'returned')),
    channel     TEXT NOT NULL CHECK (channel IN ('web', 'mobile', 'marketplace'))
);
COMMENT ON TABLE orders IS 'One row per order. Only completed orders count towards revenue.';
COMMENT ON COLUMN orders.status IS 'completed = fulfilled and kept; cancelled = never shipped; returned = shipped then refunded.';
COMMENT ON COLUMN orders.channel IS 'Where the order was placed.';

CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10, 2) NOT NULL
);
COMMENT ON TABLE order_items IS 'Line items. Line revenue = quantity * unit_price.';
COMMENT ON COLUMN order_items.unit_price IS 'Price actually paid per unit, after any discount.';

CREATE INDEX ON customers (region_id);
CREATE INDEX ON products (category_id);
CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (order_date);
CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);
