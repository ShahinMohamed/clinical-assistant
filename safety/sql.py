"""Constrain model-generated SQL before it reaches the read-only connection."""

import sqlglot
from sqlglot import exp


COHORT_TABLES = frozenset({"patients", "conditions", "observations"})
SAFE_FUNCTIONS = frozenset({
    "ABS", "AGE", "AVG", "CAST", "CEIL", "COALESCE", "COUNT", "DATE_PART",
    "DATE_TRUNC", "EXTRACT", "FLOOR", "GREATEST", "LEAST", "LOWER", "MAX",
    "MIN", "NULLIF", "ROUND", "SUM", "TRIM", "UPPER", "CASE", "IF",
    "AND", "OR", "NOT", "EXISTS",
})


def validate_cohort_sql(query):
    """Allow one aggregate SELECT over cohort tables and nonrecursive CTEs."""
    try:
        statements = sqlglot.parse(query, read="postgres")
    except sqlglot.errors.ParseError:
        raise ValueError("Provide a valid PostgreSQL SELECT query.") from None
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise ValueError("Only a single SELECT query is allowed.")
    tree = statements[0]
    forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise ValueError("Only read-only SELECT operations are allowed.")
    if any(node.args.get("recursive") for node in tree.find_all(exp.With)):
        raise ValueError("Recursive queries are not supported.")
    ctes = {node.alias_or_name for node in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if (
            not isinstance(table.this, exp.Identifier)
            or table.catalog
            or table.db not in ("", "public")
            or table.name not in COHORT_TABLES | ctes
            or (table.db and table.name not in COHORT_TABLES)
        ):
            raise ValueError("Use only the supplied cohort tables.")
    for function in tree.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name()
        if name not in SAFE_FUNCTIONS:
            raise ValueError("The query uses an unsupported SQL function.")
    if any(isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func) for node in tree.walk()):
        raise ValueError("Schema-qualified functions are not supported.")
    if not any(isinstance(node, exp.AggFunc) for node in tree.walk()):
        raise ValueError("Ask for aggregate cohort results, not individual records.")
    return query
