"""Typed representation of an introspected PostgreSQL database (Pydantic v2).

Pure structure — tables, columns, types, keys, foreign keys, comments, and enum
*definitions*. Deliberately NO row/sample data: these models describe the shape
of a database, not its contents. Produced by ``nixus.schema.introspect``.
"""
from __future__ import annotations

import warnings
from datetime import datetime

from pydantic import BaseModel, Field

# Table.schema is a required part of a table's identity (two schemas may share a
# table name), so the field is named `schema` by design. That shadows Pydantic's
# deprecated BaseModel.schema() classmethod, which only emits a one-time
# UserWarning at class-build time. Silence just that message; the field works.
warnings.filterwarnings(
    "ignore", message='Field name "schema" .*shadows an attribute', category=UserWarning
)


class Column(BaseModel):
    name: str
    # Canonical type from pg_catalog.format_type — faithful to arrays ("text[]"),
    # precision ("numeric(10,2)"), varchar length, and enum type names. NOT the
    # information_schema view, which flattens arrays/enums.
    data_type: str
    is_nullable: bool
    default: str | None = None          # column default expression, or None
    is_primary_key: bool = False
    comment: str | None = None          # from col_description, or None
    # If the column's type is an enum, its allowed values in sort order. This is
    # structural type metadata (pg_enum), NOT row data. None for non-enum columns.
    enum_values: list[str] | None = None


class ForeignKey(BaseModel):
    constraint_name: str
    from_schema: str
    from_table: str
    # ORDERED to pair positionally with to_columns: from_columns[i] references
    # to_columns[i]. Correct ordering of composite FKs is the whole point.
    from_columns: list[str]
    to_schema: str
    to_table: str
    to_columns: list[str]               # ORDERED, same length as from_columns


class Table(BaseModel):
    # Schema-qualified identity: two schemas may share a table name, so (schema,
    # name) together identify a table.
    # `schema` shadows pydantic-v1's deprecated BaseModel.schema() method, which
    # is what mypy's assignment error sees; pydantic v2 fully supports a field
    # of this name (the old method is gone at runtime), and renaming the field
    # would churn the whole introspection/embedding API for zero behavior gain.
    schema: str  # type: ignore[assignment]
    name: str
    columns: list[Column]               # in ordinal position order, NONE dropped
    primary_key: list[str] = Field(default_factory=list)  # ordered, composite-aware
    comment: str | None = None          # from obj_description, or None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


class IntrospectedSchema(BaseModel):
    database: str | None = None
    tables: list[Table] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    introspected_at: datetime = Field(default_factory=datetime.now)
