"""What the agents are told about the data besides its schema and SQL dialect: what it is, its business rules and how
to write its money. The connected database's brief comes from its semantic folder (the demo's describes
the store's sales); an uploaded file gets a plainer one (app/datasets)."""

from dataclasses import dataclass

from app import semantic


@dataclass(frozen=True)
class Brief:
    about: str  # what the data is, for the router: "an e-commerce database"
    rules: str  # business rules for the SQL agent, as a bulleted list ("" when there are none)
    time_column: str  # the date column the SQL agent's example for time series names
    money: str | None  # how the summary writes money; None when the data says nothing about currency
    out_of_scope: str  # the reply to a question about something else


def database() -> Brief:
    """The connected database: the demo's store, or your own when SEMANTIC_DIR says what it is (semantic/metrics.yml)."""
    library = semantic.load()
    about = library.about
    return Brief(about=about.data, rules=library.rules_text(), time_column=about.time_column, money=about.money,
                 out_of_scope=about.out_of_scope)
