import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import MovementType, OrderStatus, Role

T = TypeVar("T")

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Password = Annotated[str, Field(min_length=8, max_length=128)]
Phone = Annotated[str, Field(pattern=r"^\+?[0-9 -]{7,20}$")]


def _strong_password(value: str) -> str:
    if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
        raise ValueError("Password must contain at least one letter and one digit")
    return value


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


class Message(BaseModel):
    message: str


# ---------- Auth / users ----------


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    full_name: Annotated[str, Field(min_length=2, max_length=120)]
    phone: Phone | None = None

    _pw = field_validator("password")(_strong_password)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORM):
    id: int
    email: EmailStr
    full_name: str
    phone: str | None
    role: Role
    is_active: bool
    created_at: datetime


class UserUpdateIn(BaseModel):
    full_name: Annotated[str, Field(min_length=2, max_length=120)] | None = None
    phone: Phone | None = None


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: Password

    _pw = field_validator("new_password")(_strong_password)


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ForgotPasswordOut(BaseModel):
    message: str
    reset_token: str | None = Field(None, description="Only returned in dummy/dev mode (no email delivery)")


class ResetPasswordIn(BaseModel):
    token: str
    new_password: Password

    _pw = field_validator("new_password")(_strong_password)


class RoleIn(BaseModel):
    role: Role


class StatusIn(BaseModel):
    is_active: bool


# ---------- Catalog ----------


class CategoryIn(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=80)]
    description: str | None = None


class CategoryOut(ORM):
    id: int
    name: str
    description: str | None
    product_count: int = 0


class SupplierIn(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=120)]
    email: EmailStr | None = None
    phone: Phone | None = None
    address: str | None = None


class SupplierOut(ORM):
    id: int
    name: str
    email: str | None
    phone: str | None
    address: str | None


class ProductBrief(ORM):
    id: int
    sku: str
    name: str
    price: Decimal
    stock_quantity: int


class SupplierDetailOut(SupplierOut):
    products: list[ProductBrief]


SKU = Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9-]+$")]


class ProductBase(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=160)]
    description: str | None = None
    category_id: int
    supplier_id: int | None = None
    price: Money
    cost_price: Money | None = None
    reorder_level: Annotated[int, Field(ge=0)] = 10
    is_active: bool = True


class ProductCreate(ProductBase):
    sku: SKU
    stock_quantity: Annotated[int, Field(ge=0)] = 0

    @field_validator("sku")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class ProductReplace(ProductBase):
    sku: SKU

    @field_validator("sku")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class ProductPatch(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=160)] | None = None
    description: str | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    price: Money | None = None
    cost_price: Money | None = None
    reorder_level: Annotated[int, Field(ge=0)] | None = None
    is_active: bool | None = None


class ProductOut(ORM):
    id: int
    sku: str
    name: str
    description: str | None
    category_id: int
    category_name: str | None = None
    supplier_id: int | None
    price: Decimal
    cost_price: Decimal | None
    stock_quantity: int
    reorder_level: int
    stock_status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------- Inventory ----------


class InventoryItem(BaseModel):
    product_id: int
    sku: str
    name: str
    stock_quantity: int
    reorder_level: int
    stock_status: str
    stock_value: Decimal


class RestockIn(BaseModel):
    quantity: Annotated[int, Field(gt=0, le=100_000)]
    reason: str | None = Field(None, max_length=255)


class AdjustIn(BaseModel):
    change: int = Field(description="Positive to add, negative to remove")
    reason: Annotated[str, Field(min_length=3, max_length=255)]

    @field_validator("change")
    @classmethod
    def _non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("change must not be zero")
        return v


class MovementOut(ORM):
    id: int
    product_id: int
    change: int
    balance_after: int
    movement_type: MovementType
    reason: str | None
    user_id: int | None
    order_id: int | None
    created_at: datetime


# ---------- Orders ----------


class OrderItemIn(BaseModel):
    sku: SKU
    quantity: Annotated[int, Field(gt=0, le=1000)]


class OrderCreate(BaseModel):
    customer_name: Annotated[str, Field(max_length=120)] | None = None
    items: list[OrderItemIn]

    @model_validator(mode="after")
    def _normalise(self):
        for item in self.items:
            item.sku = item.sku.upper()
        return self


class OrderItemOut(ORM):
    product_id: int
    sku: str
    product_name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class OrderOut(ORM):
    id: int
    order_number: str
    user_id: int
    customer_name: str | None
    status: OrderStatus
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]


class OrderStatusIn(BaseModel):
    status: OrderStatus


# ---------- Reports ----------


class TopProduct(BaseModel):
    sku: str
    name: str
    units_sold: int
    revenue: Decimal


class SummaryOut(BaseModel):
    currency: str
    revenue: Decimal
    orders_by_status: dict[str, int]
    total_orders: int
    top_products: list[TopProduct]
    inventory_retail_value: Decimal
    inventory_cost_value: Decimal
    total_skus: int
    low_stock_skus: int
    out_of_stock_skus: int
