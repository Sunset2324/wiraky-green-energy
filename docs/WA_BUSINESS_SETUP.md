# Persiapan WhatsApp Business (Cloud API) untuk notifikasi Additional Cost

Kode sudah siap (`services/whatsapp_service.py`, `routes/whatsapp_routes.py`). Default **mode LOG**: tidak ada pesan nyata terkirim.
Mode nyata aktif hanya jika `WA_ENABLED=1` dan kredensial di bawah terisi.

## Yang dikirim ke pelanggan
| Jenis | Kapan | Template (default) |
|---|---|---|
| `BILL_ISSUED` | Tagihan diterima Finance (status RECEIVED / PAYMENT_PENDING) | `additional_cost_bill` |
| `PAYMENT_CONFIRMED` | Status PAID (callback Finance yang valid) | `additional_cost_paid` |

Aturan: satu pesan per tagihan+revisi+jenis (tidak ganda); bila sudah PAID sebelum "tagihan" terkirim, pesan tagihan dibatalkan;
revisi lama yang digantikan tidak dinotifikasi. **Pesan tidak memuat tautan pembayaran** (instruksi bayar hanya lewat kanal resmi Finance).
Kegagalan WhatsApp **tidak pernah mengubah status pembayaran**; hanya memunculkan notifikasi admin di `/dashboard`.

## Langkah di Meta (dilakukan pemilik bisnis)
1. Meta Business Manager terverifikasi + WhatsApp Business Account (WABA).
2. Daftarkan nomor WA Business (nomor tidak boleh sedang dipakai di aplikasi WhatsApp biasa) → catat **Phone number ID**.
3. Buat **System User** + token permanen dengan permission `whatsapp_business_messaging` → `WA_ACCESS_TOKEN`. Jangan pakai token sementara 24 jam.
4. Meta App > Settings > Basic → **App Secret** → `WA_APP_SECRET`.
5. Buat dua **message template**, kategori **UTILITY**, bahasa `id` (Indonesian), lalu tunggu status *Approved*:

   `additional_cost_bill` (4 variabel):
   > Halo {{1}}, pemasangan Green Energy untuk pesanan {{2}} memerlukan biaya tambahan sebesar {{3}} (ref. {{4}}) yang telah Anda setujui. Tagihan sudah diterbitkan oleh Finance Wiraky. Pembayaran hanya melalui kanal resmi Wiraky.

   `additional_cost_paid` (5 variabel):
   > Halo {{1}}, pembayaran biaya tambahan {{2}} sebesar {{3}} untuk pesanan {{4}} telah kami terima dan diverifikasi pada {{5}}. Terima kasih.

   Urutan variabel harus persis seperti di atas. Jika nama/isi template Anda berbeda, ubah `WA_TEMPLATE_*` dan `TEMPLATE_BODIES` + `_params` di kode agar sinkron.
6. Webhook (WhatsApp > Configuration): Callback URL `https://<domain-https>/api/v1/whatsapp/webhook`, Verify token = `WA_VERIFY_TOKEN`, subscribe field **messages** (status pesan datang lewat field ini). Wajib HTTPS publik (Dev Tunnels cukup untuk uji).
7. Isi `.env` (lihat `.env.example`), set `WA_ENABLED=1`, jalankan `python app.py`, cek `/whatsapp`.

## Uji bertahap
1. Mode LOG: buat pengajuan → lihat pratinjau di `/whatsapp`.
2. Mode nyata dengan nomor penguji (Meta membatasi penerima sebelum akun live): kirim ke nomor Anda sendiri.
3. Pastikan status "accepted → sent → delivered → read" muncul (webhook bekerja).

## Hal yang perlu diperhatikan
- **Opt-in**: Meta mewajibkan persetujuan pelanggan untuk menerima pesan WhatsApp. Pastikan form/surat persetujuan Wiraky mencakupnya (di luar kode).
- Biaya: pesan template berbayar per percakapan/pesan sesuai tarif Meta saat ini; cek tarif terbaru.
- `WA_API_VERSION` default `v21.0`; sesuaikan dengan versi Graph API yang masih didukung Meta saat Anda go-live.
- Antrean notifikasi **in-memory** (sama dengan data lain): hilang saat restart. Produksi butuh database.
- Pesan masuk dari pelanggan tidak diproses (tidak bisa menyetujui atau melunasi tagihan lewat WhatsApp).
- Kode ini **belum diuji terhadap server Meta sungguhan** (tidak ada kredensial di sini); diuji dengan transport tiruan sesuai bentuk respons Cloud API.
