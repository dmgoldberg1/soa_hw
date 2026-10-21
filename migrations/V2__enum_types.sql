CREATE TYPE product_status AS ENUM ('ACTIVE', 'INACTIVE', 'ARCHIVED');
CREATE TYPE order_status AS ENUM ('CREATED', 'PAYMENT_PENDING', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELED');
CREATE TYPE discount_type AS ENUM ('PERCENTAGE', 'FIXED_AMOUNT');
CREATE TYPE operation_type AS ENUM ('CREATE_ORDER', 'UPDATE_ORDER');

ALTER TABLE products DROP CONSTRAINT products_status_check;
ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE promo_codes DROP CONSTRAINT promo_codes_discount_type_check;
ALTER TABLE user_operations DROP CONSTRAINT user_operations_operation_type_check;
DROP INDEX idx_one_active_order;

ALTER TABLE products ALTER COLUMN status TYPE product_status USING status::product_status;
ALTER TABLE orders ALTER COLUMN status TYPE order_status USING status::order_status;
ALTER TABLE promo_codes ALTER COLUMN discount_type TYPE discount_type USING discount_type::discount_type;
ALTER TABLE user_operations ALTER COLUMN operation_type TYPE operation_type USING operation_type::operation_type;

CREATE UNIQUE INDEX idx_one_active_order ON orders(user_id)
    WHERE status IN ('CREATED', 'PAYMENT_PENDING');
