"""
Order Model - E-commerce order entity.

This model references:
- User (foreign key relationship)

Referenced by:
- order_service.py (order processing)
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
from decimal import Decimal

from .user import User


class OrderStatus(Enum):
    """Order processing status."""
    DRAFT = "draft"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(Enum):
    """Payment status for orders."""
    UNPAID = "unpaid"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


@dataclass
class OrderItem:
    """Individual item in an order."""
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    discount: Decimal = Decimal("0.00")
    
    @property
    def subtotal(self) -> Decimal:
        """Calculate item subtotal after discount."""
        return (self.unit_price * self.quantity) - self.discount
    
    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "quantity": self.quantity,
            "unit_price": str(self.unit_price),
            "discount": str(self.discount),
            "subtotal": str(self.subtotal),
        }


@dataclass
class ShippingAddress:
    """Shipping address for order delivery."""
    street: str
    city: str
    state: str
    postal_code: str
    country: str
    phone: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "postal_code": self.postal_code,
            "country": self.country,
            "phone": self.phone,
        }


@dataclass
class Order:
    """
    Order entity for e-commerce transactions.
    
    Attributes:
        id: Unique order identifier
        user_id: Reference to the ordering user
        items: List of order items
        status: Current order status
        payment_status: Payment processing status
        shipping_address: Delivery address
        subtotal: Sum of item prices before tax/shipping
        tax: Tax amount
        shipping_cost: Shipping charges
        total: Final order total
        notes: Customer notes
        created_at: Order creation timestamp
        updated_at: Last modification timestamp
    """
    id: int
    user_id: int
    items: List[OrderItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.DRAFT
    payment_status: PaymentStatus = PaymentStatus.UNPAID
    shipping_address: Optional[ShippingAddress] = None
    subtotal: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    shipping_cost: Decimal = Decimal("0.00")
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    
    @property
    def total(self) -> Decimal:
        """Calculate final order total."""
        return self.subtotal + self.tax + self.shipping_cost
    
    @property
    def item_count(self) -> int:
        """Get total number of items."""
        return sum(item.quantity for item in self.items)
    
    def calculate_subtotal(self) -> Decimal:
        """Recalculate subtotal from items."""
        self.subtotal = sum(item.subtotal for item in self.items)
        return self.subtotal
    
    def add_item(self, item: OrderItem) -> None:
        """Add item to order."""
        if self.status != OrderStatus.DRAFT:
            raise ValueError("Cannot modify confirmed order")
        
        self.items.append(item)
        self.calculate_subtotal()
        self.updated_at = datetime.utcnow()
    
    def remove_item(self, product_id: int) -> bool:
        """Remove item from order by product ID."""
        if self.status != OrderStatus.DRAFT:
            raise ValueError("Cannot modify confirmed order")
        
        for i, item in enumerate(self.items):
            if item.product_id == product_id:
                self.items.pop(i)
                self.calculate_subtotal()
                self.updated_at = datetime.utcnow()
                return True
        return False
    
    def confirm(self) -> None:
        """Confirm order for processing."""
        if not self.items:
            raise ValueError("Cannot confirm empty order")
        if not self.shipping_address:
            raise ValueError("Shipping address required")
        
        self.status = OrderStatus.CONFIRMED
        self.updated_at = datetime.utcnow()
    
    def cancel(self, reason: str = None) -> None:
        """Cancel order."""
        if self.status in [OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
            raise ValueError("Cannot cancel shipped/delivered order")
        
        self.status = OrderStatus.CANCELLED
        if reason:
            self.notes = f"Cancelled: {reason}"
        self.updated_at = datetime.utcnow()
    
    def can_be_modified(self) -> bool:
        """Check if order can still be modified."""
        return self.status == OrderStatus.DRAFT
    
    def to_dict(self) -> dict:
        """Convert order to dictionary representation."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "items": [item.to_dict() for item in self.items],
            "status": self.status.value,
            "payment_status": self.payment_status.value,
            "shipping_address": self.shipping_address.to_dict() if self.shipping_address else None,
            "subtotal": str(self.subtotal),
            "tax": str(self.tax),
            "shipping_cost": str(self.shipping_cost),
            "total": str(self.total),
            "item_count": self.item_count,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OrderRepository:
    """
    Repository pattern for Order persistence.
    
    In production, this would use a real database.
    """
    
    def __init__(self):
        self._orders: dict[int, Order] = {}
        self._user_orders: dict[int, List[int]] = {}  # user_id -> order_ids
        self._next_id = 1
    
    def create(self, order: Order) -> Order:
        """Create a new order."""
        order.id = self._next_id
        self._next_id += 1
        
        self._orders[order.id] = order
        
        if order.user_id not in self._user_orders:
            self._user_orders[order.user_id] = []
        self._user_orders[order.user_id].append(order.id)
        
        return order
    
    def find_by_id(self, order_id: int) -> Optional[Order]:
        """Find order by ID."""
        return self._orders.get(order_id)
    
    def find_by_user(self, user_id: int, status: Optional[OrderStatus] = None) -> List[Order]:
        """Find all orders for a user."""
        order_ids = self._user_orders.get(user_id, [])
        orders = [self._orders[oid] for oid in order_ids if oid in self._orders]
        
        if status:
            orders = [o for o in orders if o.status == status]
        
        return orders
    
    def update(self, order: Order) -> Order:
        """Update existing order."""
        if order.id not in self._orders:
            raise ValueError(f"Order {order.id} not found")
        
        order.updated_at = datetime.utcnow()
        self._orders[order.id] = order
        
        return order
