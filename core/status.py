"""Status internal Additional Cost (Standar API Integrated, bagian 09)."""

NOT_REQUIRED = "NOT_REQUIRED"        # tidak ada Additional Cost; tidak ada penagihan
APPROVED = "APPROVED"                # disetujui, siap dikirim
SENT = "SENT"                        # pengajuan API terkirim
RECEIVED = "RECEIVED"                # diterima Finance (HTTP 202) - BUKAN bukti sudah bayar
PAYMENT_PENDING = "PAYMENT_PENDING"  # tagihan menunggu pembayaran
PAID = "PAID"                        # pembayaran terverifikasi dan direkonsiliasi
FAILED = "FAILED"
EXPIRED = "EXPIRED"
REFUNDED = "REFUNDED"
REVIEW = "REVIEW"                    # perlu pemeriksaan manual; tidak diubah otomatis

ALL_STATUSES = (NOT_REQUIRED, APPROVED, SENT, RECEIVED, PAYMENT_PENDING,
                PAID, FAILED, EXPIRED, REFUNDED, REVIEW)

# Status pembayaran yang boleh dikirim Finance di callback (payment_status)
PAYMENT_STATUSES = ("PENDING", "PAID", "FAILED", "EXPIRED", "REFUNDED")

_ALLOWED = {
    NOT_REQUIRED: set(),
    APPROVED: {SENT, REVIEW},
    SENT: {RECEIVED, REVIEW},
    RECEIVED: {PAYMENT_PENDING, PAID, FAILED, EXPIRED, REVIEW},
    PAYMENT_PENDING: {PAID, FAILED, EXPIRED, REVIEW},
    FAILED: {PAYMENT_PENDING, PAID, EXPIRED, REVIEW},
    # PAID setelah EXPIRED/FAILED hanya boleh jika Finance mengonfirmasi bukti bank yang sah
    EXPIRED: {PAID, REVIEW},
    PAID: {REFUNDED, REVIEW},
    REFUNDED: {REVIEW},
    REVIEW: set(),
}


def can_transition(old: str, new: str) -> bool:
    """Perpindahan status yang sama dianggap aman (idempotent)."""
    if old == new:
        return True
    return new in _ALLOWED.get(old, set())


# Status yang masih "aktif" (belum final) untuk keperluan rekonsiliasi / cegah tagihan ganda
OPEN_STATUSES = (APPROVED, SENT, RECEIVED, PAYMENT_PENDING)
