from enum import StrEnum

from sqlalchemy import Enum as SQLEnum


def enum_column_type[E: StrEnum](enum_cls: type[E], length: int = 32) -> SQLEnum:
    """VARCHAR-backed enum column (not a native MySQL ENUM).

    New members can be added later with a plain code change + additive,
    no-lock migration, instead of a MySQL ENUM column rebuild. A CHECK
    constraint still enforces valid values at the DB level.
    """
    return SQLEnum(
        enum_cls,
        length=length,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
