import sqlglot
from sqlglot import exp

BLOCKED_STATEMENT_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,  # catches VACUUM, ANALYZE, COPY, etc.
)


def is_read_only_sql(sql: str) -> tuple[bool, str]:
    """
    Parse the SQL using sqlglot and verify it contains only SELECT
    (or WITH ... SELECT) statements. No INSERT, UPDATE, DELETE, DROP,
    CREATE, ALTER, TRUNCATE, or raw COMMAND statements are permitted.

    Returns:
        (True, "") if the SQL is safe to execute as read-only.
        (False, reason) if any blocked statement type is found.
    """
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except Exception as e:
        return False, f"SQL parse error: {e!s}"

    if not statements:
        return False, "No valid SQL statement found."

    for statement in statements:
        if statement is None:
            continue
        if isinstance(statement, BLOCKED_STATEMENT_TYPES):
            return False, f"Statement type not permitted: {type(statement).__name__}"
        # Allow only Select and With (CTEs that resolve to SELECT)
        if not isinstance(statement, (exp.Select, exp.With)):
            return False, f"Only SELECT statements are permitted. Got: {type(statement).__name__}"
        # For WITH statements, verify the final expression is a SELECT
        if isinstance(statement, exp.With):
            inner = statement.args.get("expression")
            if inner is not None and not isinstance(inner, exp.Select):
                return False, f"WITH clause must resolve to a SELECT. Got: {type(inner).__name__}"

    return True, ""
