"""
Order Service - Business logic for order management.

Dependencies:
- models.order (Order, OrderRepository, OrderItem, OrderStatus)
- models.user (User)
- services.user_service (UserService)

Referenced by:
- order_controller (API endpoints - to be created)
"""
import logging
from typing import Optional, List
from datetime import datetime
from decimal import Decimal

from ..models.order import Order, OrderRepository, OrderItem, OrderStatus, PaymentStatus, ShippingAddress
from ..models.user import User
from .user_service import UserService, UserNotFoundError

logger = logging.getLogger(__name__)


class OrderServiceError(Exception):
    """Base exception for order service errors."""
    pass


class OrderNotFoundError(OrderServiceError):
    """Raised when order is not found."""
    pass


class InvalidOrderError(OrderServiceError):
    """Raised when order operation is invalid."""
    pass


class PermissionDeniedError(OrderServiceError):
    """Raised when user lacks permission for operation."""
    pass


class OrderService:
    """
    Service layer for order management operations.
    
    Provides business logic for:
    - Order creation and modification
    - Order processing workflow
    - Payment integration
    - Shipping calculations
    - Order history
    """
    
    # Tax rate (simplified - would be location-based in production)
    TAX_RATE = Decimal("0.08")  # 8%
    
    # Free shipping threshold
    FREE_SHIPPING_THRESHOLD = Decimal("50.00")
    STANDARD_SHIPPING_COST = Decimal("5.99")
    
    def __init__(
        self,
        order_repository: Optional[OrderRepository] = None,
        user_service: Optional[UserService] = None
    ):
        """
        Initialize OrderService.
        
        Args:
            order_repository: OrderRepository instance
            user_service: UserService instance for user validation
        """
        self.repository = order_repository or OrderRepository()
        self.user_service = user_service or UserService()
        logger.info("OrderService initialized")
    
    def create_order(self, user_id: int) -> Order:
        """
        Create a new empty order for user.
        
        Args:
            user_id: ID of the user placing the order
            
        Returns:
            Created Order instance
            
        Raises:
            UserNotFoundError: If user doesn't exist
            InvalidOrderError: If user cannot place orders
        """
        # Validate user exists and is active
        user = self.user_service.get_user(user_id)
        
        if not user.is_active():
            raise InvalidOrderError("Only active users can place orders")
        
        order = Order(
            id=0,  # Assigned by repository
            user_id=user_id,
            status=OrderStatus.DRAFT
        )
        
        created_order = self.repository.create(order)
        logger.info(f"Created order {created_order.id} for user {user_id}")
        
        return created_order
    
    def get_order(self, order_id: int, user_id: Optional[int] = None) -> Order:
        """
        Get order by ID with optional ownership check.
        
        Args:
            order_id: Order identifier
            user_id: If provided, verify order belongs to this user
            
        Returns:
            Order instance
            
        Raises:
            OrderNotFoundError: If order doesn't exist
            PermissionDeniedError: If user doesn't own the order
        """
        order = self.repository.find_by_id(order_id)
        
        if not order:
            raise OrderNotFoundError(f"Order not found: {order_id}")
        
        if user_id and order.user_id != user_id:
            raise PermissionDeniedError("You don't have access to this order")
        
        return order
    
    def add_item(
        self,
        order_id: int,
        product_id: int,
        product_name: str,
        quantity: int,
        unit_price: Decimal,
        user_id: int
    ) -> Order:
        """
        Add item to order.
        
        Args:
            order_id: Target order ID
            product_id: Product identifier
            product_name: Product display name
            quantity: Quantity to add
            unit_price: Price per unit
            user_id: User making the modification
            
        Returns:
            Updated Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if not order.can_be_modified():
            raise InvalidOrderError("Order cannot be modified")
        
        item = OrderItem(
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price
        )
        
        order.add_item(item)
        self._recalculate_totals(order)
        
        updated_order = self.repository.update(order)
        logger.info(f"Added item {product_id} to order {order_id}")
        
        return updated_order
    
    def update_item_quantity(
        self,
        order_id: int,
        product_id: int,
        quantity: int,
        user_id: int
    ) -> Order:
        """
        Update quantity of an item in order.
        
        Args:
            order_id: Target order ID
            product_id: Product to update
            quantity: New quantity (0 to remove)
            user_id: User making the modification
            
        Returns:
            Updated Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if not order.can_be_modified():
            raise InvalidOrderError("Order cannot be modified")
        
        if quantity <= 0:
            return self.remove_item(order_id, product_id, user_id)
        
        for item in order.items:
            if item.product_id == product_id:
                item.quantity = quantity
                break
        else:
            raise InvalidOrderError(f"Item {product_id} not in order")
        
        order.calculate_subtotal()
        self._recalculate_totals(order)
        
        return self.repository.update(order)
    
    def remove_item(self, order_id: int, product_id: int, user_id: int) -> Order:
        """
        Remove item from order.
        
        Args:
            order_id: Target order ID
            product_id: Product to remove
            user_id: User making the modification
            
        Returns:
            Updated Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if not order.can_be_modified():
            raise InvalidOrderError("Order cannot be modified")
        
        if not order.remove_item(product_id):
            raise InvalidOrderError(f"Item {product_id} not in order")
        
        self._recalculate_totals(order)
        
        return self.repository.update(order)
    
    def set_shipping_address(
        self,
        order_id: int,
        address: ShippingAddress,
        user_id: int
    ) -> Order:
        """
        Set shipping address for order.
        
        Args:
            order_id: Target order ID
            address: Shipping address details
            user_id: User making the modification
            
        Returns:
            Updated Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if not order.can_be_modified():
            raise InvalidOrderError("Order cannot be modified")
        
        order.shipping_address = address
        self._recalculate_totals(order)
        
        return self.repository.update(order)
    
    def confirm_order(self, order_id: int, user_id: int) -> Order:
        """
        Confirm order and begin processing.
        
        Args:
            order_id: Order to confirm
            user_id: User confirming the order
            
        Returns:
            Confirmed Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if order.status != OrderStatus.DRAFT:
            raise InvalidOrderError("Only draft orders can be confirmed")
        
        if not order.items:
            raise InvalidOrderError("Cannot confirm empty order")
        
        if not order.shipping_address:
            raise InvalidOrderError("Shipping address required")
        
        # Final recalculation
        self._recalculate_totals(order)
        
        # Confirm order
        order.confirm()
        
        updated_order = self.repository.update(order)
        logger.info(f"Order {order_id} confirmed, total: {order.total}")
        
        return updated_order
    
    def process_payment(self, order_id: int, payment_method: str) -> Order:
        """
        Process payment for order.
        
        In production, this would integrate with payment gateway.
        
        Args:
            order_id: Order to pay for
            payment_method: Payment method identifier
            
        Returns:
            Order with updated payment status
        """
        order = self.repository.find_by_id(order_id)
        
        if not order:
            raise OrderNotFoundError(f"Order not found: {order_id}")
        
        if order.status != OrderStatus.CONFIRMED:
            raise InvalidOrderError("Only confirmed orders can be paid")
        
        # Mock payment processing
        # In production: call payment gateway
        order.payment_status = PaymentStatus.PAID
        order.status = OrderStatus.PROCESSING
        
        logger.info(f"Payment processed for order {order_id}: {payment_method}")
        
        return self.repository.update(order)
    
    def update_order_status(self, order_id: int, status: OrderStatus) -> Order:
        """
        Update order status (admin operation).
        
        Args:
            order_id: Order to update
            status: New status
            
        Returns:
            Updated Order instance
        """
        order = self.repository.find_by_id(order_id)
        
        if not order:
            raise OrderNotFoundError(f"Order not found: {order_id}")
        
        old_status = order.status
        order.status = status
        order.updated_at = datetime.utcnow()
        
        logger.info(f"Order {order_id} status: {old_status.value} -> {status.value}")
        
        return self.repository.update(order)
    
    def cancel_order(self, order_id: int, user_id: int, reason: str = None) -> Order:
        """
        Cancel an order.
        
        Args:
            order_id: Order to cancel
            user_id: User cancelling the order
            reason: Cancellation reason
            
        Returns:
            Cancelled Order instance
        """
        order = self.get_order(order_id, user_id)
        
        if order.status in [OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
            raise InvalidOrderError("Cannot cancel shipped/delivered order")
        
        order.cancel(reason)
        
        # If payment was made, mark for refund
        if order.payment_status == PaymentStatus.PAID:
            order.payment_status = PaymentStatus.REFUNDED
            logger.info(f"Order {order_id} refund initiated")
        
        logger.info(f"Order {order_id} cancelled: {reason}")
        
        return self.repository.update(order)
    
    def get_user_orders(
        self,
        user_id: int,
        status: Optional[OrderStatus] = None,
        limit: int = 50
    ) -> List[Order]:
        """
        Get orders for a user.
        
        Args:
            user_id: User's identifier
            status: Filter by order status
            limit: Maximum results
            
        Returns:
            List of Order instances
        """
        orders = self.repository.find_by_user(user_id, status)
        return orders[:limit]
    
    def _recalculate_totals(self, order: Order) -> None:
        """Recalculate order subtotal, tax, and shipping."""
        # Subtotal
        order.calculate_subtotal()
        
        # Tax
        order.tax = order.subtotal * self.TAX_RATE
        
        # Shipping
        if order.subtotal >= self.FREE_SHIPPING_THRESHOLD:
            order.shipping_cost = Decimal("0.00")
        else:
            order.shipping_cost = self.STANDARD_SHIPPING_COST
