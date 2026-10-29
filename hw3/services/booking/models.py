from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    user_id: UUID
    flight_id: UUID
    passenger_name: Annotated[str, Field(min_length=1, max_length=200)]
    passenger_email: Annotated[EmailStr, Field(max_length=254)]
    seat_count: Annotated[int, Field(strict=True, ge=1, le=1000)]

    @field_validator('passenger_name')
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError('Passenger name must not be blank')
        return value


class BookingResponse(BaseModel):
    id: UUID
    user_id: UUID
    flight_id: UUID
    passenger_name: str
    passenger_email: str
    seat_count: int
    total_price: Decimal
    status: Literal['CONFIRMED', 'CANCELLED']
    created_at: datetime
