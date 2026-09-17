import re

# These patterns match the start of any SQL statement that modifies data or schema.
# They are intentionally conservative — false positives are safe, false negatives are not.
#
# This is the regex defense-in-depth guard used by generate_sql to reject any
# non-SELECT statement the LLM might emit. NIXUS is read-only; write *requests*
# are refused upstream at the scope gate, and any write SQL that still slips
# through generation is blocked here (alongside the sqlglot AST check).
_WRITE_PATTERNS = re.compile(
    r"""
    ^\s*                        # optional leading whitespace
    (
        INSERT\s+INTO           |
        UPDATE\s+\w             |
        DELETE\s+FROM           |
        DROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|TRIGGER|SEQUENCE) |
        TRUNCATE\s+             |
        CREATE\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|TRIGGER|SEQUENCE) |
        ALTER\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|TRIGGER|SEQUENCE) |
        REPLACE\s+INTO          |
        MERGE\s+INTO            |
        CALL\s+\w               |
        EXECUTE\s+\w            |
        GRANT\s+                |
        REVOKE\s+
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)


def contains_write_operation(sql: str) -> tuple[bool, str]:
    """
    Rule-based regex scan of SQL for any data-modifying or schema-modifying
    statement. This is a defense-in-depth check, not the primary safety gate.

    Returns:
        (True, matched_keyword) if a write operation is detected.
        (False, "") if no write operation is found.
    """
    match = _WRITE_PATTERNS.search(sql)
    if match:
        return True, match.group(0).strip()
    return False, ""
