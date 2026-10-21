ALTER TABLE products DROP COLUMN seller_id;
ALTER TABLE orders DROP CONSTRAINT orders_user_id_fkey;
ALTER TABLE user_operations DROP CONSTRAINT user_operations_user_id_fkey;
DROP TABLE users;
