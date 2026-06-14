# Dokumentasi Sinkronisasi Coretax (`sync_coretax.py`)

File `sync_coretax.py` merupakan script mandiri (standalone) yang berfungsi sebagai **jembatan otomatis** antara API sistem POS dengan kebutuhan pembuatan file **XML Coretax**.

Daripada mengunduh Excel, membuka isinya, lalu memindahkannya ke database secara manual, script ini mengotomatiskan seluruh proses tersebut dalam hitungan detik.

---

## ⚙️ Cara Menjalankan

Sebelum menjalankan, pastikan semua *library* pendukung sudah terinstal. Jika belum, jalankan perintah ini di terminal:
```bash
python3 -m pip install httpx pandas openpyxl pymysql python-dotenv
```

Lalu jalankan script dengan perintah:
```bash
python3 sync_coretax.py
```

---

## 🛠 Alur Kerja & Fitur Utama

### 1. Pembersihan Data Lama (Refresh)
Setiap kali dijalankan, script ini akan secara otomatis men-drop (menghapus) tabel Coretax yang lama (`coretax_faktur` dan `coretax_detail_faktur`) di MySQL. Tujuannya adalah memastikan data yang ada di database murni hanya berisi data terbaru dari tarikan hari ini, sehingga data tidak menumpuk atau *redundant*.

### 2. Request & Download ke Memori
Script membaca file `.env` untuk mengambil URL API dan Token Bearer. Script kemudian melakukan request ke API Coretax untuk mendapatkan link file Excel terbaru.
File Excel tersebut **tidak disimpan ke dalam hardisk** komputer Anda, melainkan didownload langsung ke dalam memori komputer (RAM). Hal ini membuat proses lebih bersih dan cepat.

### 3. Ekstraksi Data Header (Faktur)
Script membaca tab/sheet **"Faktur"** di dalam memori Excel tersebut.
- Karena di format Excel Coretax baris 1 dan 2 adalah baris kosong/header file, script secara cerdas akan melewatinya dan mulai membaca dari baris ke-3 (baris nama kolom yang sebenarnya).
- Script otomatis membuat tabel `coretax_faktur` di MySQL dengan nama-nama kolom yang dinamis menyesuaikan isi Excel.
- Seluruh baris data dimasukkan sekaligus ke dalam tabel MySQL.

### 4. Ekstraksi Data Item (DetailFaktur)
Script berpindah ke tab/sheet **"DetailFaktur"**.
- Serupa dengan Faktur, script otomatis membuat tabel `coretax_detail_faktur` di MySQL.
- Data barang/jasa untuk setiap faktur diekstrak dan dimasukkan ke dalam database.

### 5. Penguncian Relasi untuk Keperluan XML Coretax
Ini adalah fitur kunci untuk memastikan olahan data XML Anda valid:
- Di dalam Excel bawaan, antara Faktur dan DetailFaktur dihubungkan oleh angka di kolom **`Baris`**.
- Untuk memastikan keamanan data saat pembuatan XML (agar relasi tidak teracak dengan tarikan hari lain), script otomatis menyisipkan kolom **`batch_id`** (berisi UUID acak untuk satu kali run) dan kolom **`kategori_tarikan`**.

Nantinya, program pembuat XML Anda dapat melakukan proses *Query* / *JOIN* dengan aman menggunakan format berikut:

```sql
SELECT * FROM coretax_faktur f
JOIN coretax_detail_faktur d
  ON f.baris = d.baris AND f.batch_id = d.batch_id;
```

Dengan struktur tabel seperti ini, proses konversi ke format XML Coretax dijamin 100% presisi dan tidak akan ada item yang tertukar ke faktur yang salah.
