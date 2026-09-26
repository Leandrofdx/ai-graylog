-- Assistente de Vendas (lab) — schema estilo Colombo
CREATE TABLE IF NOT EXISTS staff (
    staff_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    password    TEXT NOT NULL,
    store_id    TEXT NOT NULL DEFAULT '1001'
);

CREATE TABLE IF NOT EXISTS products (
    item_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    price       NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    stock       INT NOT NULL CHECK (stock >= 0)
);

CREATE TABLE IF NOT EXISTS customers (
    cpf         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    account_num TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pre_sales (
    id          TEXT PRIMARY KEY,
    staff_id    TEXT NOT NULL REFERENCES staff(staff_id),
    cpf         TEXT NOT NULL REFERENCES customers(cpf),
    item_id     TEXT NOT NULL REFERENCES products(item_id),
    qty         INT NOT NULL CHECK (qty > 0),
    amount      NUMERIC(12,2) NOT NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_presales_staff ON pre_sales(staff_id, created_at);

INSERT INTO staff (staff_id, name, password, store_id) VALUES
    ('vendedor1', 'Ana Vendedora', 'lab123', '1001'),
    ('vendedor2', 'Bruno Vendedor', 'lab123', '1001')
ON CONFLICT (staff_id) DO NOTHING;

INSERT INTO products (item_id, name, price, stock) VALUES
    ('SKU-7',  'Notebook Pro 15',     4599.90, 25),
    ('SKU-42', 'Mouse Wireless',       129.90, 120),
    ('SKU-99', 'Monitor 27 Full HD',  1899.00, 10),
    ('SKU-15', 'Teclado Mecânico',     499.00, 45),
    ('SKU-88', 'Smartphone X',        2499.00, 18)
ON CONFLICT (item_id) DO NOTHING;

INSERT INTO customers (cpf, name, account_num) VALUES
    ('52998224725', 'Cliente Lab Um', 'ACC-100'),
    ('39053344705', 'Cliente Lab Dois', 'ACC-200')
ON CONFLICT (cpf) DO NOTHING;
