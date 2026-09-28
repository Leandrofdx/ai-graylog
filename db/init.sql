-- Assistente de Vendas (lab) — schema estilo Colombo (CDC / CDCI / CP)
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
    stock       INT NOT NULL CHECK (stock >= 0),
    category    TEXT NOT NULL DEFAULT 'Geral'
);

CREATE TABLE IF NOT EXISTS customers (
    cpf         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    account_num TEXT NOT NULL,
    wage        NUMERIC(12,2) NOT NULL DEFAULT 3500.00
);

CREATE TABLE IF NOT EXISTS pre_sales (
    id              TEXT PRIMARY KEY,
    staff_id        TEXT NOT NULL REFERENCES staff(staff_id),
    cpf             TEXT NOT NULL REFERENCES customers(cpf),
    item_id         TEXT NOT NULL REFERENCES products(item_id),
    qty             INT NOT NULL CHECK (qty > 0),
    amount          NUMERIC(12,2) NOT NULL,
    payment_type    TEXT NOT NULL DEFAULT 'avista',
    tender_type_id  INT,
    installments    INT,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credit_limits (
    cpf             TEXT PRIMARY KEY REFERENCES customers(cpf),
    available_limit NUMERIC(12,2) NOT NULL,
    used_limit      NUMERIC(12,2) NOT NULL DEFAULT 0,
    product_types   TEXT[] NOT NULL DEFAULT ARRAY['CDC','CDCI','CP']
);

CREATE TABLE IF NOT EXISTS financial_plans (
    id                              SERIAL PRIMARY KEY,
    product_type                    TEXT NOT NULL, -- CDC | CDCI
    tender_type_id                  INT NOT NULL,
    name                            TEXT NOT NULL,
    num_of_payment                  INT NOT NULL,
    interest_rate                   NUMERIC(8,4) NOT NULL,
    retail_financial_external_approval INT NOT NULL,
    financeira                      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id              TEXT PRIMARY KEY,
    sale_id         TEXT REFERENCES pre_sales(id),
    staff_id        TEXT NOT NULL REFERENCES staff(staff_id),
    cpf             TEXT NOT NULL REFERENCES customers(cpf),
    product_type    TEXT NOT NULL, -- CDC | CDCI
    tender_type_id  INT NOT NULL,
    installments    INT NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    financeira      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS personal_credits (
    id              TEXT PRIMARY KEY,
    staff_id        TEXT NOT NULL REFERENCES staff(staff_id),
    cpf             TEXT NOT NULL REFERENCES customers(cpf),
    amount          NUMERIC(12,2) NOT NULL,
    installments    INT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    financeira      TEXT NOT NULL DEFAULT 'Crediare',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_presales_staff ON pre_sales(staff_id, created_at);
CREATE INDEX IF NOT EXISTS idx_proposals_cpf ON proposals(cpf, created_at);
CREATE INDEX IF NOT EXISTS idx_cp_cpf ON personal_credits(cpf, created_at);

INSERT INTO staff (staff_id, name, password, store_id) VALUES
    ('vendedor1', 'Ana Vendedora', 'lab123', '1001'),
    ('vendedor2', 'Bruno Vendedor', 'lab123', '1001')
ON CONFLICT (staff_id) DO NOTHING;

-- Estoque no teto (999999) — InsufficientStock só sob bug/qty absurda.
INSERT INTO products (item_id, name, price, stock, category) VALUES
    ('SKU-7',  'Notebook Pro 15',     4599.90, 999999, 'Informática'),
    ('SKU-42', 'Mouse Wireless',       129.90, 999999, 'Acessórios'),
    ('SKU-99', 'Monitor 27 Full HD',  1899.00, 999999, 'Informática'),
    ('SKU-15', 'Teclado Mecânico',     499.00, 999999, 'Acessórios'),
    ('SKU-88', 'Smartphone X',        2499.00, 999999, 'Telefonia')
ON CONFLICT (item_id) DO UPDATE SET
    category = EXCLUDED.category,
    stock = GREATEST(products.stock, EXCLUDED.stock),
    price = EXCLUDED.price,
    name = EXCLUDED.name;

INSERT INTO customers (cpf, name, account_num, wage) VALUES
    ('52998224725', 'Cliente Lab Um', 'ACC-100', 4200.00),
    ('39053344705', 'Cliente Lab Dois', 'ACC-200', 2800.00)
ON CONFLICT (cpf) DO NOTHING;

-- Crédito lab “infinito” + todas as linhas (CDC/CDCI/CP) nos dois CPFs.
INSERT INTO credit_limits (cpf, available_limit, used_limit, product_types) VALUES
    ('52998224725', 999999999.00, 0.00, ARRAY['CDC','CDCI','CP']),
    ('39053344705', 999999999.00, 0.00, ARRAY['CDC','CDCI','CP'])
ON CONFLICT (cpf) DO UPDATE SET
    available_limit = EXCLUDED.available_limit,
    used_limit = EXCLUDED.used_limit,
    product_types = EXCLUDED.product_types;

-- CDC = Crediare / Financeira 12 (tender 2006, approval 1)
-- CDCI = Fin25 / Financeira 25 (tender 2011, approval 3)
INSERT INTO financial_plans (product_type, tender_type_id, name, num_of_payment, interest_rate, retail_financial_external_approval, financeira)
SELECT * FROM (VALUES
    ('CDC',  2006, 'CDC Crediare 6x',  6,  1.8900, 1, 'Financeira 12'),
    ('CDC',  2006, 'CDC Crediare 12x', 12, 2.1500, 1, 'Financeira 12'),
    ('CDC',  2006, 'CDC Crediare 18x', 18, 2.4500, 1, 'Financeira 12'),
    ('CDC',  2006, 'CDC Crediare 24x', 24, 2.7900, 1, 'Financeira 12'),
    ('CDCI', 2011, 'CDCI Fin25 12x',   12, 1.9900, 3, 'Financeira 25'),
    ('CDCI', 2011, 'CDCI Fin25 20x',   20, 2.3500, 3, 'Financeira 25'),
    ('CDCI', 2011, 'CDCI Fin25 24x',   24, 2.6500, 3, 'Financeira 25'),
    ('CDCI', 2011, 'CDCI Fin25 36x',   36, 2.9900, 3, 'Financeira 25')
) AS v(product_type, tender_type_id, name, num_of_payment, interest_rate, retail_financial_external_approval, financeira)
WHERE NOT EXISTS (SELECT 1 FROM financial_plans LIMIT 1);
