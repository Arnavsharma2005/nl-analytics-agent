"""What the agents are told about the data besides its schema and SQL dialect: what it is, its business rules and how
to write its money. The demo's brief describes the store's sales database; an uploaded file gets a
plainer one (app/datasets)."""

from dataclasses import dataclass

from app import semantic


@dataclass(frozen=True)
class Brief:
    about: str  # what the data is, for the router: "an e-commerce database"
    rules: str  # business rules for the SQL agent, as a bulleted list ("" when there are none)
    time_column: str  # the date column the SQL agent's example for time series names
    money: str | None  # how the summary writes money; None when the data says nothing about currency
    out_of_scope: str  # the reply to a question about something else


def demo() -> Brief:
    """The store's sales data that the public demo answers questions about."""
    return Brief(
        about="an e-commerce database",
        rules=semantic.load().rules_text(),
        time_column="order_date",
        money="Money is in Indian rupees; write amounts like ₹1.2 Cr, ₹45.3 L or ₹8,450.",
        out_of_scope=(
            "I can only answer questions about the sales data: orders, revenue, products, categories, customers and "
            "regions for 2024-2025. Try something like \"Monthly revenue in 2025\" or \"Top 5 products by profit\"."
        ),
    )
