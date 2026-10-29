-- ============================================
-- SKEMA DATABASE - STANDAR API INTEGRATED v1.0
-- Green Energy - IndiHome Aura
-- ============================================

-- 1. Tabel Orders (Data survei & pelanggan)
CREATE TABLE IF NOT EXISTS orders (
    id VARCHAR(50) PRIMARY KEY,                    -- Contoh: WIR-2026-0001
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(20) NOT NULL,
    package_name VARCHAR(100),                     -- Contoh: IndiHome Aura 3500
    surveyor_notes TEXT,
    reason TEXT,                                   -- Alasan teknis additional cost
    status ENUM('DRAFT', 'APPROVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED') DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 2. Tabel Additional Costs (Menggantikan tabel invoices lama)
CREATE TABLE IF NOT EXISTS additional_costs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    additional_cost_id VARCHAR(50) NOT NULL,       -- Contoh: AC-2026-0001
    revision INT NOT NULL DEFAULT 1,               -- Revisi ke-1, ke-2, dst
    
    -- Data item
    items_json JSON NOT NULL,                      -- Array of additional_items
    total_amount DECIMAL(15, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'IDR',
    
    -- Approval
    approval_status ENUM('DRAFT', 'APPROVED', 'REJECTED', 'REVISED') DEFAULT 'DRAFT',
    approval_reference VARCHAR(100),               -- Contoh: APR-2026-0001
    approved_at TIMESTAMP NULL,
    
    -- Status pengiriman ke Finance
    status ENUM(
        'NOT_REQUIRED',      -- Tidak ada additional cost
        'APPROVED',          -- Siap dikirim ke Finance
        'SENT',              -- Sudah dikirim, menunggu ACK
        'RECEIVED',          -- Diterima Finance (202 Accepted)
        'PAYMENT_PENDING',   -- Tagihan menunggu pembayaran
        'PAID',              -- Pembayaran terverifikasi
        'FAILED',            -- Transaksi gagal
        'EXPIRED',           -- Kedaluwarsa
        'REFUNDED',          -- Refund
        'REVIEW'             -- Perlu pemeriksaan
    ) DEFAULT 'DRAFT',
    
    -- Data dari Finance
    finance_reference VARCHAR(100),                -- Contoh: FIN-2026-0001
    invoice_id VARCHAR(100),                       -- Invoice ID dari Finance
    payment_reference VARCHAR(100),                -- Contoh: TRX-DEMO-0001
    amount_paid DECIMAL(15, 2),
    paid_at TIMESTAMP NULL,
    verified_by VARCHAR(100),
    
    -- Event tracking
    event_id VARCHAR(100),                         -- Contoh: EVT-AC-2026-0001-R01
    last_sent_at TIMESTAMP NULL,
    last_response_code INT,
    retry_count INT DEFAULT 0,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- Constraints
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    UNIQUE KEY uk_additional_cost_revision (additional_cost_id, revision),
    UNIQUE KEY uk_event_id (event_id),
    INDEX idx_order (order_id),
    INDEX idx_status (status),
    INDEX idx_finance_ref (finance_reference)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 3. Tabel Finance Events (Untuk idempotency webhook)
CREATE TABLE IF NOT EXISTS finance_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id VARCHAR(100) NOT NULL UNIQUE,         -- Harus unik untuk idempotency
    event_type VARCHAR(50) NOT NULL,               -- PAYMENT_CONFIRMED, dll
    order_id VARCHAR(50),
    additional_cost_id VARCHAR(50),
    revision INT,
    
    -- Payment details
    invoice_id VARCHAR(100),
    payment_reference VARCHAR(100),
    amount_paid DECIMAL(15, 2),
    currency VARCHAR(3),
    paid_at TIMESTAMP NULL,
    
    -- Processing result
    processing_result ENUM('SUCCESS', 'DUPLICATE', 'ERROR', 'NOT_FOUND', 'AMOUNT_MISMATCH') NOT NULL,
    
    -- Raw payload untuk audit
    raw_payload JSON NOT NULL,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_event_id (event_id),
    INDEX idx_order (order_id),
    INDEX idx_additional_cost (additional_cost_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 4. Tabel Audit Logs (Compliance & traceability)
CREATE TABLE IF NOT EXISTS audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(50),
    additional_cost_id VARCHAR(50),
    event_type VARCHAR(50) NOT NULL,               -- PAYMENT_CONFIRMED, INVOICE_CREATED, dll
    action VARCHAR(100) NOT NULL,
    detail TEXT,
    actor VARCHAR(100) DEFAULT 'SYSTEM',
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_order (order_id),
    INDEX idx_event_type (event_type),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 5. Tabel Outbox (Untuk retry terkendali sesuai PDF Halaman 10)
CREATE TABLE IF NOT EXISTS outbox_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id VARCHAR(100) NOT NULL,
    payload JSON NOT NULL,
    status ENUM('PENDING', 'SENT', 'FAILED', 'RETRYING') DEFAULT 'PENDING',
    retry_count INT DEFAULT 0,
    last_attempt_at TIMESTAMP NULL,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_event_id (event_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;