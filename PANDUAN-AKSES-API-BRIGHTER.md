# Panduan Akses API Brighter (Koffiesoft)

Dokumen ini disusun dari **pengujian langsung ke API produksi pada 6 Agustus 2026**, bukan dari
dokumentasi. Setiap perilaku di bawah sudah diverifikasi dengan permintaan nyata.

**Ringkasan temuan:** API tidak dikunci dan tidak dibatasi. Seluruh 6.943 halaman ledger POS
berhasil ditarik tanpa satu pun penolakan (0 kegagalan). Kesan "data tidak bisa ditarik" hampir
selalu berasal dari tiga perilaku server yang tidak lazim — dijelaskan di bagian **Jebakan**.
Baca bagian itu lebih dulu; ia menghemat berhari-hari.

---

## 1. Host

| Keperluan | Host |
|---|---|
| Master/cabang (publik, tanpa login) | `https://brighter-api.koffiesoft.com` |
| Transaksi & laporan | `https://brighter-kairatu-api.koffiesoft.com` |

Keduanya menunjuk IP yang sama (`103.167.136.25`, nginx), versi API `0.0.36`. Nama host yang
mengandung "kairatu" **tidak berarti data hanya cabang Kairatu** — ia mengembalikan seluruh cabang.
Pastikan Anda memakai host transaksi untuk endpoint transaksi.

## 2. Autentikasi

`POST /login` dengan **`multipart/form-data`** — bukan JSON. Mengirim JSON akan gagal.

```
POST /login
Content-Type: multipart/form-data
  username=<user>
  password=<sandi>

200 OK -> {"access_token": "..."}
```

Sertakan pada setiap permintaan berikutnya:

```
Authorization: Bearer <access_token>
Accept: application/json
```

Token bisa kedaluwarsa di tengah penarikan panjang. Tangani `401`/`403` dengan **login ulang lalu
ulangi permintaan**, jangan langsung menyimpulkan akses ditolak.

## 3. Bentuk respons

Semua endpoint membungkus datanya:

```json
{
  "status": {"code": 200, "message": "Data Berhasil Ditampilkan."},
  "data":   [ ... ],
  "paging": {"total_pages": 6943}
}
```

Isi yang Anda butuhkan ada di `data`, bukan di akar. Paginasi lewat `?page=N`, 20 baris per halaman.
Berhenti saat `data` kosong **atau** `page >= paging.total_pages`.

## 4. Endpoint

| Endpoint | `cabang_id` | `dari`/`sampai` | Cara pakai |
|---|---|---|---|
| `GET /master/cabang` | — | — | Publik, tanpa token |
| `GET /transaksi/pos` | **diabaikan** | **diabaikan** | Seluruh ledger; saring sendiri |
| `GET /transaksi/pelunasan_piutang` | **diabaikan** | dihormati | Panggil sekali, semua cabang |
| `GET /transaksi/pelunasan_hutang` | **diabaikan** | dihormati | Panggil sekali, semua cabang |
| `GET /laporan/lap_pembelian/` | **diabaikan** | dihormati | **Wajib garis miring akhir** |
| `GET /laporan/lap_bukti_transfer` | dihormati | dihormati | Harus di-loop per cabang |
| `GET /akuntansi/kasbank_masuk` | dihormati | dihormati | Berpaginasi, bisa ratusan halaman |

Format tanggal: `YYYY-MM-DD`.

## 5. Daftar cabang

| `cabang_id` | Nama |
|---|---|
| 1 | CSB - Kobisonta |
| 2 | CSB - Bula |
| 4 | CSB - Mandiri |
| 5 | CSB - Kairatu |
| 7 | CSB - Piru |

**Nomornya tidak berurutan** — tidak ada 3 dan 6. `for i in range(1, 8)` akan menembak id yang
tidak ada. (Satu baris ber-`cabang_id=6` pernah muncul; perlakukan id tak dikenal sebagai data,
jangan sampai membuat program berhenti.)

---

## 6. JEBAKAN — baca bagian ini

### 6.1 `/transaksi/pos` mengabaikan seluruh parameter penyaring

Terbukti langsung: permintaan dengan `dari=2026-03-21&sampai=2026-05-27&cabang_id=1` mengembalikan
**5 nomor nota pertama yang identik** dengan permintaan tanpa parameter apa pun — keduanya berisi
data tanggal terbaru (6 Agustus 2026).

Server **selalu** mengirim seluruh ledger (±138.000 baris), terurut dari yang terbaru. Siapa pun
yang mengandalkan parameter tanggal akan selalu menerima data hari ini, lalu menyimpulkan rentang
lampaunya kosong. **Inilah penyebab paling umum laporan "data tidak tersedia".**

Penyaringan tanggal dan cabang **harus dilakukan di sisi klien**.

### 6.2 Ledger POS TIDAK terurut tanggal secara ketat

Ini yang paling berbahaya, karena kesalahannya senyap.

Penarikan kami sempat berhenti di halaman 3.821 setelah menemui deretan halaman bertanggal lebih
tua — padahal data dalam rentang **muncul lagi** di halaman 3.879–4.059, sebanyak 3.619 nota, yakni
**13% dari total yang seharusnya**. Program yang berhenti pada halaman "tua" pertama akan
melewatkan data ini tanpa error apa pun.

**Jangan** berhenti pada halaman tua pertama. Pilih salah satu:
- pindai seluruh halaman `1..total_pages` (paling aman), atau
- berhenti hanya setelah **puluhan halaman berturut-turut** seluruhnya di luar rentang.

### 6.3 `lap_pembelian` wajib memakai garis miring akhir

`/laporan/lap_pembelian` **tanpa** `/` memicu redirect HTTPS→HTTP. Hampir semua klien HTTP —
`httpx`, `requests`, `axios`, `fetch` — **membuang header `Authorization`** saat redirect berpindah
skema, demi keamanan. Hasilnya `401`, yang terbaca persis seperti "kredensial ditolak" padahal
kredensialnya benar.

Pakai `/laporan/lap_pembelian/` dan aktifkan `follow_redirects` / `allow_redirects`.

---

## 7. Struktur data POS

Tiap nota (`data[i]`):

| Field | Arti |
|---|---|
| `jproduk_id` | ID internal |
| `jproduk_nobukti` | Nomor nota, mis. `SB/NI/2603-0794` |
| `jproduk_tanggal` | `YYYY-MM-DD` |
| `jproduk_cabang_id` | Cabang — **baca dari sini**, bukan dari parameter |
| `jproduk_cust` | ID pelanggan |
| `jproduk_keterangan` | Catatan kasir |
| `jproduk_stat_dok` | `Tertutup` / `Batal` / `Terbuka` |
| `jproduk_totalbiaya` | Nilai nota |
| `jproduk_bayar` | Nilai bayar pada header |
| `jproduk_request_batal_*`, `jproduk_approval_batal_*` | Jejak pembatalan |
| `jproduk_cara_bayar_data` | **Array** baris pembayaran |

Tiap baris di `jproduk_cara_bayar_data`:

| Field | Arti |
|---|---|
| `djual_cbayar_nama` | `tunai` / `qris_barcode` / `transfer` / `card` |
| `djual_nilai_bayar_rp` | Nilai baris ini |
| `djual_transfer_bank_id`, `djual_transfer_nama` | Bank & nama pengirim |
| `djual_card_jenis`, `djual_card_edc`, `djual_card_no` | Detail kartu/EDC |

**Satu nota bisa punya beberapa baris bayar.** Contoh nyata:

```
KRT/AZ/2604-0298  (27 Apr 2026, Kairatu)
    transfer  Rp 31.000.000
    tunai     Rp    524.000
```

Jumlahkan **per nota**, bukan per baris, bila Anda mencocokkan ke mutasi bank — bank menyetel per nota.

### Nota kredit / piutang

Sebagian nota punya baris bayar bernilai **0**, sehingga `jumlah nilai bayar < jproduk_totalbiaya`.
Itu **penjualan kredit**, bukan data hilang; pelunasannya tercatat di `/transaksi/pelunasan_piutang`.
Pada pengujian kami, 1.008 dari 27.262 nota berperilaku begini, senilai Rp 6,06 miliar.

### Nota batal

Nota berstatus `Batal` **tetap dikembalikan API** (`jproduk_stat_dok = "Batal"`). Ia tidak hilang
dari feed, jadi Anda wajib menyaringnya sendiri bila tak ingin ikut terhitung.

---

## 8. Contoh implementasi (Python)

```python
import asyncio, datetime as dt, httpx

BASE = "https://brighter-kairatu-api.koffiesoft.com"
DARI, SAMPAI = dt.date(2026, 3, 21), dt.date(2026, 5, 27)
CABANG = {"1": "Kobisonta", "2": "Bula", "4": "Mandiri", "5": "Kairatu", "7": "Piru"}


async def login(c, user, sandi):
    r = await c.post(f"{BASE}/login", data={"username": user, "password": sandi})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}",
            "Accept": "application/json"}


async def tarik_pos(user, sandi):
    # follow_redirects WAJIB (lihat 6.3)
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as c:
        H = await login(c, user, sandi)

        r = await c.get(f"{BASE}/transaksi/pos", headers=H, params={"page": 1})
        total = int(r.json()["paging"]["total_pages"])

        hasil = {}
        for page in range(1, total + 1):          # pindai SEMUA halaman (lihat 6.2)
            r = await c.get(f"{BASE}/transaksi/pos", headers=H, params={"page": page})
            if r.status_code in (401, 403):        # token kedaluwarsa -> login ulang
                H = await login(c, user, sandi)
                continue
            r.raise_for_status()
            batch = r.json().get("data") or []
            if not batch:
                break
            for it in batch:
                tgl = dt.date.fromisoformat(str(it["jproduk_tanggal"])[:10])
                if not (DARI <= tgl <= SAMPAI):    # saring di sisi klien (lihat 6.1)
                    continue
                hasil[it["jproduk_nobukti"]] = {
                    "tanggal": tgl,
                    "cabang": CABANG.get(str(it.get("jproduk_cabang_id")), "?"),
                    "status": it.get("jproduk_stat_dok"),
                    "total": float(it.get("jproduk_totalbiaya") or 0),
                    "bayar": [(b.get("djual_cbayar_nama"),
                               float(b.get("djual_nilai_bayar_rp") or 0))
                              for b in (it.get("jproduk_cara_bayar_data") or [])],
                }
        return hasil


# Catatan kinerja: 6.943 halaman berurutan memakan waktu lama. Dengan 6 permintaan
# paralel (asyncio.Semaphore) seluruh ledger selesai dalam ~12 menit. Jangan lebih
# agresif dari itu; endpoint ini berat di sisi server.
```

**Jangan mulai dari halaman 1 bila hanya butuh satu rentang lampau.** Cari titik masuknya dengan
*binary search* pada nomor halaman (bandingkan tanggal di halaman tengah), lalu telusuri dari sana.
Kami mencapai halaman 3.472 hanya dalam ~12 permintaan. Tetap berlakukan aturan 6.2 saat berhenti.

---

## 9. Bukti bahwa API berfungsi normal

Diuji 6 Agustus 2026 dengan akun standar:

- `POST /login` → **200**, token diterima
- `GET /transaksi/pos` → **200**, `total_pages = 6943`, tanggal terbaru = hari pengujian
- Seluruh ledger dipindai: **6.943 halaman, 0 gagal, 0 penolakan 401/403**
- Terkumpul **128.167 nota** untuk rentang 10 Jan – 31 Jul 2026
- Rincian 21 Mar – 27 Mei 2026: 24.669 baris tunai, 1.303 QRIS, 1.096 transfer, 68 kartu

Bila Anda menerima `401` atau daftar kosong, periksa berurutan: (1) host transaksi sudah benar,
(2) login memakai `multipart/form-data`, (3) `follow_redirects` aktif, (4) `lap_pembelian` memakai
garis miring akhir, (5) penyaringan tanggal dilakukan di sisi klien, (6) penelusuran halaman tidak
berhenti dini. Enam hal itu menjelaskan hampir semua kasus.

## 10. Catatan kondisi data (bukan masalah API)

- **POS Piru baru aktif 15 Mei 2026.** Sebelum itu praktis kosong (April 2 nota; Mei 2.350;
  Juni 3.845; Juli 4.625). Rentang sebelum pertengahan Mei memang tidak berisi apa-apa.
- **Kobisonta, Bula, Mandiri tidak punya transaksi 21–27 Maret 2026**; data mulai 28 Maret.
- Kolom pelanggan sering berisi ID generik yang dipakai berulang untuk pembeli umum — jangan
  diperlakukan sebagai identitas pelanggan yang unik.
