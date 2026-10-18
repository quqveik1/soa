CREATE TYPE user_role AS ENUM ('USER', 'SELLER', 'ADMIN');
CREATE TYPE product_status AS ENUM ('ACTIVE', 'INACTIVE', 'ARCHIVED');
CREATE TYPE order_status AS ENUM (
    'CREATED', 'PAYMENT_PENDING', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELED'
);
CREATE TYPE discount_type AS ENUM ('PERCENTAGE', 'FIXED_AMOUNT');
CREATE TYPE operation_type AS ENUM ('CREATE_ORDER', 'UPDATE_ORDER');

CREATE TABLE users (
    id UUID CONSTRAINT users_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(64) NOT NULL,
    password_hash TEXT NOT NULL,
    role user_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_username_unique UNIQUE (username),
    CONSTRAINT users_username_not_empty CHECK (char_length(username) > 0)
);

CREATE TABLE refresh_tokens (
    id UUID CONSTRAINT refresh_tokens_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT refresh_tokens_user_fk FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE products (
    id UUID CONSTRAINT products_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description VARCHAR(4000),
    price DECIMAL(12,2) NOT NULL,
    stock INTEGER NOT NULL,
    category VARCHAR(100) NOT NULL,
    status product_status NOT NULL,
    seller_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT products_name_not_empty CHECK (char_length(name) > 0),
    CONSTRAINT products_price_positive CHECK (price > 0),
    CONSTRAINT products_stock_non_negative CHECK (stock >= 0),
    CONSTRAINT products_category_not_empty CHECK (char_length(category) > 0),
    CONSTRAINT products_seller_fk FOREIGN KEY (seller_id) REFERENCES users (id)
);

CREATE INDEX products_status_idx ON products (status);

CREATE TABLE promo_codes (
    id UUID CONSTRAINT promo_codes_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(20) NOT NULL,
    discount_type discount_type NOT NULL,
    discount_value DECIMAL(12,2) NOT NULL,
    min_order_amount DECIMAL(12,2) NOT NULL,
    max_uses INTEGER NOT NULL,
    current_uses INTEGER NOT NULL DEFAULT 0,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT promo_codes_code_unique UNIQUE (code),
    CONSTRAINT promo_codes_code_format CHECK (code ~ '^[A-Z0-9_]{4,20}$'),
    CONSTRAINT promo_codes_discount_value_positive CHECK (discount_value > 0),
    CONSTRAINT promo_codes_percentage_range CHECK (
        discount_type <> 'PERCENTAGE' OR discount_value <= 100
    ),
    CONSTRAINT promo_codes_min_order_amount_non_negative CHECK (min_order_amount >= 0),
    CONSTRAINT promo_codes_max_uses_positive CHECK (max_uses > 0),
    CONSTRAINT promo_codes_current_uses_range CHECK (current_uses BETWEEN 0 AND max_uses),
    CONSTRAINT promo_codes_valid_interval CHECK (valid_from <= valid_until)
);

CREATE TABLE orders (
    id UUID CONSTRAINT orders_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    status order_status NOT NULL DEFAULT 'CREATED',
    promo_code_id UUID,
    total_amount DECIMAL(12,2) NOT NULL,
    discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT orders_promo_code_fk FOREIGN KEY (promo_code_id) REFERENCES promo_codes (id),
    CONSTRAINT orders_total_amount_non_negative CHECK (total_amount >= 0),
    CONSTRAINT orders_discount_amount_non_negative CHECK (discount_amount >= 0)
);

CREATE UNIQUE INDEX orders_one_active_per_user_idx ON orders (user_id)
    WHERE status IN ('CREATED', 'PAYMENT_PENDING');

CREATE TABLE order_items (
    id UUID CONSTRAINT order_items_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL,
    product_id UUID NOT NULL,
    quantity INTEGER NOT NULL,
    price_at_order DECIMAL(12,2) NOT NULL,
    CONSTRAINT order_items_order_fk FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE,
    CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT order_items_quantity_range CHECK (quantity BETWEEN 1 AND 999),
    CONSTRAINT order_items_price_positive CHECK (price_at_order > 0),
    CONSTRAINT order_items_product_unique UNIQUE (order_id, product_id)
);

CREATE TABLE user_operations (
    id UUID CONSTRAINT user_operations_pk PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    operation_type operation_type NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_operations_user_fk FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE INDEX user_operations_latest_idx
    ON user_operations (user_id, operation_type, created_at DESC);

CREATE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER products_set_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER orders_set_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
